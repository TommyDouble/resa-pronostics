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


def _refresh():
    from app.trophies import refresh_trophy_awards

    async def _c():
        async with get_db() as db:
            await refresh_trophy_awards(db)
            await db.commit()
    run(_c())


def _unlocked_keys(pid):
    async def _c():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT DISTINCT trophy_key FROM trophy_awards WHERE participant_id=?", (pid,)
            )
            return {r["trophy_key"] for r in await rows.fetchall()}
    return run(_c())


def test_trophy_cabinet_renders_noto_assets_without_native_emoji(client):
    """Refonte premium : cabinet rendu avec des assets Noto SVG locaux,
    sans sprite PNG, sans paliers, et SANS emoji natif comme rendu principal."""
    from app.trophies import TROPHIES
    pid, token = _make_participant()
    html = client.get(f"/p/{token}/profil").text
    cabinet = _cabinet_fragment(html)
    # Plus de sprite PNG ni de paliers/médailles.
    assert "trophy-symbol-sprite" not in html
    assert "/static/img/trophy-silhouettes/" not in html
    assert "t-medal--" not in cabinet  # plus de badge de palier (ancien système)
    assert 'class="t-pips"' not in cabinet and 'class="t-bar"' not in cabinet
    # Une tuile par trophée du catalogue.
    assert cabinet.count('class="trophy ') == len(TROPHIES)
    # Icônes servies en assets Noto locaux (pas de CDN, pas d'emoji natif principal).
    assert 'class="t-noto"' in cabinet
    assert "/static/img/trophies/noto/" in cabinet
    assert "🥄" not in cabinet and "🔮" not in cabinet  # plus d'emoji système
    # Secret verrouillé : cadenas élégant + libellé masqué, pas d'emoji 🔒.
    assert 'class="t-lock"' in cabinet and "Trophée secret" in cabinet
    assert "🔒" not in cabinet
    # Hooks de rareté propagés sur les tuiles.
    assert "rar-legendary" in cabinet and "rar-anti" in cabinet


def test_trophy_cabinet_tooltip_keeps_dated_history(client):
    pid, token = _make_participant()

    async def _seed():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO trophy_awards
                   (participant_id, trophy_key, detail, sporting_day)
                   VALUES (?, 'grimpeur', '2044-07-07', '2044-07-07')""",
                (pid,),
            )
            await db.commit()

    run(_seed())
    cabinet = _cabinet_fragment(client.get(f"/p/{token}/profil").text)
    assert "Historique : Journée du" in cabinet
    assert "7 juillet." in cabinet


def test_journee_parfaite_requires_all_day_matches_predicted(client):
    pid, _ = _make_participant()
    base_number = 9900000 + pid * 10
    mids = [
        _make_match(base_number + i, "2044-07-07", result="team1", score_team1=1, score_team2=0)
        for i in range(4)
    ]
    for mid in mids[:3]:
        _predict(pid, mid, "team1", 1, 0)
    _refresh()
    assert "journee_parfaite" not in _unlocked_keys(pid)

    _predict(pid, mids[3], "team1", 1, 0)
    _refresh()
    assert "journee_parfaite" in _unlocked_keys(pid)
