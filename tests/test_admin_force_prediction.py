"""Admin: encoder un prono au nom d'un participant (match verrouillé inclus)."""
import uuid

from app.database import get_db
from tests.conftest import run


def _make_participant(name="Force Cible"):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                (name, f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _make_match(number, phase="group", scored=False):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight,
                                        score_team1, score_team2, result)
                   VALUES (?,?, '2000-01-01', '12:00', 'Mexique', 'Afrique du Sud', 1, ?, ?, ?)""",
                (number, phase,
                 2 if scored else None, 0 if scored else None, "team1" if scored else None),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _prediction(pid, mid):
    async def _q():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM predictions WHERE participant_id=? AND match_id=?",
                (pid, mid),
            )
            pred = await row.fetchone()
            score_row = await db.execute(
                "SELECT points FROM scores WHERE participant_id=? AND match_id=?",
                (pid, mid),
            )
            score = await score_row.fetchone()
            return (dict(pred) if pred else None, score["points"] if score else None)

    return run(_q())


def test_force_prediction_on_locked_match(admin_client):
    pid = _make_participant()
    mid = _make_match(920001)
    resp = admin_client.post(
        "/admin/pronostics/force",
        data={"participant_id": pid, "match_id": mid,
              "score_team1": 2, "score_team2": 0},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    pred, _ = _prediction(pid, mid)
    assert pred is not None
    assert pred["exact_score_team1"] == 2 and pred["exact_score_team2"] == 0
    assert pred["prediction"] == "team1"
    assert pred["admin_entered"] == 1


def test_force_prediction_recalculates_when_result_known(admin_client):
    pid = _make_participant("Force Recalc")
    mid = _make_match(920002, scored=True)
    resp = admin_client.post(
        "/admin/pronostics/force",
        data={"participant_id": pid, "match_id": mid,
              "score_team1": 2, "score_team2": 0},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    pred, points = _prediction(pid, mid)
    assert pred["admin_entered"] == 1
    # Score exact, poids 1 : 2 (bon résultat) + 2 (bonus exact)
    assert points == 4


def test_force_prediction_overwrites_existing(admin_client):
    pid = _make_participant("Force Écrase")
    mid = _make_match(920003)

    async def _seed():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                     exact_score_team1, exact_score_team2) VALUES (?,?,'team2',0,1)""",
                (pid, mid),
            )
            await db.commit()

    run(_seed())
    admin_client.post(
        "/admin/pronostics/force",
        data={"participant_id": pid, "match_id": mid,
              "score_team1": 3, "score_team2": 1},
        follow_redirects=False,
    )
    pred, _ = _prediction(pid, mid)
    assert pred["exact_score_team1"] == 3 and pred["exact_score_team2"] == 1
    assert pred["prediction"] == "team1"
    assert pred["admin_entered"] == 1


def test_force_prediction_knockout_draw_requires_qualifier(admin_client):
    pid = _make_participant("Force KO")
    mid = _make_match(920004, phase="round_of_16")
    admin_client.post(
        "/admin/pronostics/force",
        data={"participant_id": pid, "match_id": mid,
              "score_team1": 1, "score_team2": 1},
        follow_redirects=False,
    )
    pred, _ = _prediction(pid, mid)
    assert pred is None  # refusé sans équipe qualifiée

    admin_client.post(
        "/admin/pronostics/force",
        data={"participant_id": pid, "match_id": mid,
              "score_team1": 1, "score_team2": 1,
              "qualifier_prediction": "team2"},
        follow_redirects=False,
    )
    pred, _ = _prediction(pid, mid)
    assert pred["qualifier_prediction"] == "team2"


def test_force_prediction_rejects_invalid_score(admin_client):
    pid = _make_participant("Force Invalide")
    mid = _make_match(920005)
    admin_client.post(
        "/admin/pronostics/force",
        data={"participant_id": pid, "match_id": mid,
              "score_team1": 31, "score_team2": 0},
        follow_redirects=False,
    )
    pred, _ = _prediction(pid, mid)
    assert pred is None
