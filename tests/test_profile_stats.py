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


def _make_match(number, match_date, team1="France", team2="Brésil"):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'group', ?, '12:00', ?, ?, 1)""",
                (number, match_date, team1, team2),
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
