"""Les stats de profil ne doivent pas révéler les pronos des matchs non verrouillés."""
import uuid

from app.database import get_db
from app.routers.pages import _build_profile
from tests.conftest import run


def _make_participant():
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                ("Profil Stats", f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid, token

    return run(_create())


def _make_match(number, match_date, team1="France", team2="Brésil", result=None,
                score_team1=None, score_team2=None):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight,
                                        score_team1, score_team2, result)
                   VALUES (?, 'group', ?, '12:00', ?, ?, 1, ?, ?, ?)""",
                (number, match_date, team1, team2, score_team1, score_team2, result),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _predict(pid, mid, prediction, s1, s2):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                                            exact_score_team1, exact_score_team2)
                   VALUES (?,?,?,?,?)""",
                (pid, mid, prediction, s1, s2),
            )
            await db.commit()

    run(_create())


def _profile(pid):
    async def _q():
        async with get_db() as db:
            return await _build_profile(pid, db)

    return run(_q())


def _cabinet_fragment(html):
    start = html.index("<!-- Cabinet à trophées -->")
    end = html.index("<!-- Stats fun -->", start)
    return html[start:end]


def _fun_stat(profile, label):
    return next((s for s in profile["fun_stats"] if s["label"] == label), None)


def test_draw_attempts_ignore_unlocked_matches(client):
    pid, _ = _make_participant()
    future = _make_match(930001, "2099-01-01")
    _predict(pid, future, "draw", 1, 1)

    profile = _profile(pid)
    assert _fun_stat(profile, "Matchs nuls tentés / réussis") is None

    past = _make_match(930002, "2000-01-01")
    _predict(pid, past, "draw", 0, 0)

    profile = _profile(pid)
    stat = _fun_stat(profile, "Matchs nuls tentés / réussis")
    assert stat is not None
    assert stat["value"].startswith("1 /")  # seul le match verrouillé compte


def test_favorite_pick_ignores_unlocked_matches(client):
    pid, _ = _make_participant()
    future = _make_match(930003, "2099-01-01", "Argentine", "Portugal")
    _predict(pid, future, "team1", 2, 0)

    profile = _profile(pid)
    assert _fun_stat(profile, "Équipe la plus jouée gagnante") is None

    past = _make_match(930004, "2000-01-01", "Argentine", "Portugal")
    _predict(pid, past, "team1", 1, 0)

    profile = _profile(pid)
    stat = _fun_stat(profile, "Équipe la plus jouée gagnante")
    assert stat is not None
    assert "Argentine" in stat["value"]
    assert "(1×)" in stat["value"]  # le match futur n'est pas compté


def test_home_mini_ranking_links_to_profiles(client):
    pid, token = _make_participant()
    html = client.get(f"/p/{token}").text
    assert f'href="/p/{token}/profil' in html
    assert 'class="rrow' in html


def test_limited_profile_hides_trophy_cabinet_for_other_viewer(client):
    target_id, _ = _make_participant()
    _, viewer_token = _make_participant()

    async def _limit():
        async with get_db() as db:
            await db.execute(
                "UPDATE participants SET profile_visibility='limited' WHERE id=?",
                (target_id,),
            )
            await db.commit()

    run(_limit())
    html = client.get(f"/p/{viewer_token}/profil/{target_id}").text
    assert "Profil limité aux stats publiques" in html
    assert "Cabinet à trophées" not in html


def test_trophy_cabinet_uses_stable_svg_emblems_without_tier_emoji(client):
    pid, token = _make_participant()
    base_number = 9800000 + pid * 10
    for i in range(2):
        mid = _make_match(
            base_number + i,
            f"2000-02-0{i + 1}",
            result="team1",
            score_team1=2,
            score_team2=1,
        )
        _predict(pid, mid, "team1", 2, 1)

    html = client.get(f"/p/{token}/profil").text
    cabinet = _cabinet_fragment(html)
    assert html.count('class="trophy-symbol-sprite"') == 1
    for symbol in (
        "first_step",
        "present",
        "marathon",
        "loyal",
        "sniper",
        "bonus_king",
        "so_close",
        "streak",
        "draw_king",
        "last_minute",
        "climber",
        "perfect_day",
        "lock",
    ):
        assert f'id="trophy-symbol-{symbol}"' in html
        assert f'id="trophy-symbol-mask-{symbol}"' in html
        assert f'/static/img/trophy-silhouettes/{symbol}.png' in html
    assert cabinet.count('class="trophy ') == 12
    assert cabinet.count('class="trophy-emblem-frame"') == 12
    assert cabinet.count('class="trophy-symbol ') == 12
    assert 'href="#trophy-symbol-first_step"' in cabinet
    assert 'href="#trophy-symbol-sniper"' in cabinet
    assert 'href="#trophy-symbol-lock"' in cabinet
    assert "trophy-symbol-wrap--legacy" not in cabinet
    assert 'class="t-glyph' not in cabinet
    assert 't-medal--chocolat' in cabinet and ">Ch<" in cabinet
    assert "Progression : 2/5 vers bronze" in cabinet
    assert 'class="t-bar"' in cabinet
    assert 'class="t-pips"' in cabinet
    assert not any(mark in cabinet for mark in ("🥉", "🥈", "🥇", "💎", "🍫", "🏅"))
    assert "\ufe0f" not in cabinet


def test_perfect_day_requires_all_result_matches_predicted(client):
    pid, _ = _make_participant()
    base_number = 9900000 + pid * 10
    mids = [
        _make_match(base_number + i, "2000-01-02", result="team1", score_team1=1, score_team2=0)
        for i in range(4)
    ]
    for mid in mids[:3]:
        _predict(pid, mid, "team1", 1, 0)

    profile = _profile(pid)
    perfect = next(t for t in profile["trophies"] if t["key"] == "perfect_day")
    assert perfect["unlocked"] is False

    _predict(pid, mids[3], "team1", 1, 0)
    profile = _profile(pid)
    perfect = next(t for t in profile["trophies"] if t["key"] == "perfect_day")
    assert perfect["unlocked"] is True
