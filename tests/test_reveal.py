"""W1.1 — Reveal du jour v2 : journée sportive, gate d'encodage, fenêtre, /api/reveal/seen."""
from datetime import timedelta

from app.database import get_db
from app.routers.pages import _reveal_window_data
from app.timeutils import now_utc, sporting_day
from tests.conftest import run


def _reset_matches():
    """Isole le scénario : la fenêtre du reveal cible tout depuis le dernier reveal,
    donc on repart d'une table matches vide (cascade scores/predictions)."""
    async def _c():
        async with get_db() as db:
            await db.execute("DELETE FROM matches")
            await db.commit()
    run(_c())


def _yesterday_match(number, hour, result=None, s1=None, s2=None):
    """Match daté d'hier (UTC) à l'heure donnée → déjà joué (kicked off)."""
    d = (now_utc() - timedelta(days=1)).strftime("%Y-%m-%d")

    async def _c():
        async with get_db() as db:
            cur = await db.execute(
                """INSERT INTO matches (match_number, phase, group_name, match_date, kickoff_time,
                                        team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?, 'group', 'Groupe R', ?, ?, 'France', 'Brésil', 1, ?, ?, ?)""",
                (number, d, f"{hour:02d}:00:00", s1, s2, result),
            )
            await db.commit()
            return cur.lastrowid

    return run(_c())


def _encode(mid, s1, s2, result):
    async def _c():
        async with get_db() as db:
            await db.execute(
                "UPDATE matches SET score_team1=?, score_team2=?, result=? WHERE id=?",
                (s1, s2, result, mid),
            )
            await db.commit()
    run(_c())


def _pred_score(pid, mid, es1, es2, points, prediction="team1"):
    async def _c():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                                            exact_score_team1, exact_score_team2)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(participant_id, match_id) DO UPDATE SET
                     prediction=excluded.prediction,
                     exact_score_team1=excluded.exact_score_team1,
                     exact_score_team2=excluded.exact_score_team2""",
                (pid, mid, prediction, es1, es2),
            )
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, mid, points),
            )
            await db.commit()
    run(_c())


def _last_revealed(pid):
    async def _g():
        async with get_db() as db:
            row = await db.execute(
                "SELECT last_revealed_date FROM participants WHERE id=?", (pid,)
            )
            return (await row.fetchone())["last_revealed_date"]
    return run(_g())


def _window(pid):
    async def _g():
        async with get_db() as db:
            return await _reveal_window_data(db, pid)
    return run(_g())


def test_sporting_day_groups_overnight():
    # Un match de soirée et un match d'après-minuit partagent la même journée sportive.
    evening = {"match_date": "2026-06-14", "kickoff_time": "19:00:00"}  # ~21h Bruxelles
    overnight = {"match_date": "2026-06-15", "kickoff_time": "02:00:00"}  # ~04h Bruxelles
    assert sporting_day(evening) == sporting_day(overnight)


def test_reveal_waits_for_full_encoding_then_available(client, participant):
    _reset_matches()
    a = _yesterday_match(961001, 20, result="team1", s1=2, s2=0)  # encodé
    b = _yesterday_match(961002, 22)                              # joué mais PAS encodé
    _pred_score(participant["id"], a, 2, 0, 5)

    # Un match joué non encodé dans le périmètre → on attend (pas de reveal).
    assert _window(participant["id"]) is None

    _encode(b, 1, 1, "draw")  # on encode le dernier match de la nuit
    win = _window(participant["id"])
    assert win is not None
    assert win["match_count"] == 2  # les 2 matchs de la nuit, ensemble


def test_reveal_page_renders_sequence_and_evolution(client, participant):
    _reset_matches()
    a = _yesterday_match(961010, 20, result="team1", s1=2, s2=0)
    b = _yesterday_match(961011, 22, result="draw", s1=1, s2=1)
    _pred_score(participant["id"], a, 2, 0, 5)   # exact
    _pred_score(participant["id"], b, 0, 2, 1, prediction="team2")

    win = _window(participant["id"])
    assert win["total_points"] == 6
    assert win["evolution"] and win["evolution"]["after"]

    html = client.get(f"/p/{participant['token']}/reveal").text
    assert "data-reveal" in html
    assert html.count("data-reveal-match") == 2
    assert "data-reveal-final" in html
    assert "+6 pts" in html


def test_reveal_seen_advances_pointer(client, participant):
    _reset_matches()
    a = _yesterday_match(961020, 20, result="team1", s1=2, s2=0)
    _pred_score(participant["id"], a, 2, 0, 5)
    assert _window(participant["id"]) is not None

    res = client.post(f"/api/reveal/seen?token={participant['token']}")
    assert res.status_code == 200
    # Le repère a avancé à la journée sportive du lot → plus rien à révéler.
    assert _last_revealed(participant["id"]) is not None
    assert _window(participant["id"]) is None


def test_home_entry_and_story_promo(client, participant):
    _reset_matches()
    a = _yesterday_match(961030, 20, result="team1", s1=2, s2=0)
    _pred_score(participant["id"], a, 2, 0, 5)

    html = client.get(f"/p/{participant['token']}").text
    assert "reveal-entry" in html
    assert "Le Reveal du jour est prêt" in html
    # La story promo (parcours multi-écrans) reste rendue indépendamment.
    assert "data-story-feature" in html
    assert "rp-demo" in html
