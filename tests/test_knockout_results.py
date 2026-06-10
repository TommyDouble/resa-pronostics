from app.database import get_db
from app.settings_store import KNOCKOUT_OPEN_KEY, set_setting
from tests.conftest import run


def open_knockout_predictions():
    async def _open():
        async with get_db() as db:
            await set_setting(db, KNOCKOUT_OPEN_KEY, "1")
            await db.commit()

    return run(_open())


def get_match_score(participant_id, match_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT points FROM scores WHERE participant_id=? AND match_id=?",
                (participant_id, match_id),
            )
            result = await row.fetchone()
            return result["points"] if result else None

    return run(_get())


def get_match_result(match_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
            result = await row.fetchone()
            return dict(result) if result else None

    return run(_get())


def create_knockout_match(participant_id):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_16', '2099-07-01', '20:00',
                           'Espagne', 'Allemagne', 2)""",
                (930000 + participant_id,),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def test_knockout_prediction_blocked_until_opened(admin_client, participant):
    match_id = create_knockout_match(participant["id"])

    blocked = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 1,
            "exact_score_team2": 0,
        },
    )
    assert blocked.status_code == 403
    assert "phase finale" in blocked.json()["detail"]


def test_knockout_draw_result_requires_qualifier_and_scores_by_winner(admin_client, participant):
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions()

    response = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 2,
            "exact_score_team2": 2,
            "qualifier_prediction": "team1",
        },
    )
    assert response.status_code == 200

    missing_qualifier = admin_client.post(
        f"/admin/resultats/{match_id}",
        data={"score_team1": "2", "score_team2": "2"},
        follow_redirects=False,
    )
    assert missing_qualifier.status_code == 303
    assert get_match_result(match_id)["result"] is None

    wrong_qualifier = admin_client.post(
        f"/admin/resultats/{match_id}",
        data={"score_team1": "2", "score_team2": "2", "qualifier_winner": "team2"},
        follow_redirects=False,
    )
    assert wrong_qualifier.status_code == 303
    assert get_match_score(participant["id"], match_id) == 0

    corrected = admin_client.post(
        f"/admin/resultats/{match_id}/correct",
        data={"score_team1": "2", "score_team2": "2", "qualifier_winner": "team1"},
        follow_redirects=False,
    )
    assert corrected.status_code == 303
    assert get_match_score(participant["id"], match_id) == 6
    assert get_match_result(match_id)["qualifier_winner"] == "team1"
