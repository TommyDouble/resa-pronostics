"""Integration tests: pre-tournament guard + admin answers + scoring."""
import uuid

from app.database import get_db
from app.players import get_scorer_choices
from app.scoring import get_rankings
from tests.conftest import run


def get_pt_row(participant_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM pre_tournament_predictions WHERE participant_id=?",
                (participant_id,),
            )
            result = await row.fetchone()
            return dict(result) if result else None

    return run(_get())


def set_deadline_future():
    async def _set():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO app_settings (key, value) VALUES ('pre_tournament_deadline', '2099-01-01T00:00:00')
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
            )
            await db.commit()

    run(_set())


class TestWinnerFinalistGuard:
    def test_same_team_rejected(self, client, participant):
        set_deadline_future()
        response = client.post(
            f"/p/{participant['token']}/pre-tournoi",
            data={
                "winner": "France",
                "finalist": "France",
                "top_scorer": "",
                "revelation": "",
                "total_goals": "140",
                "action": "submit",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error=winner_finalist" in response.headers["location"]
        assert get_pt_row(participant["id"]) is None

    def test_different_teams_accepted(self, client, participant):
        set_deadline_future()
        scorer = get_scorer_choices()[0]
        response = client.post(
            f"/p/{participant['token']}/pre-tournoi",
            data={
                "winner": "France",
                "finalist": "Brésil",
                "top_scorer": scorer,
                "revelation": "Maroc",
                "total_goals": "140",
                "action": "submit",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "error" not in response.headers["location"]
        row = get_pt_row(participant["id"])
        assert row["winner"] == "France"
        assert row["finalist"] == "Brésil"
        assert row["submitted"] == 1

    def test_unknown_team_rejected(self, client, participant):
        set_deadline_future()
        response = client.post(
            f"/p/{participant['token']}/pre-tournoi",
            data={
                "winner": "Atlantide",
                "finalist": "",
                "top_scorer": "",
                "revelation": "",
                "total_goals": "0",
                "action": "draft",
            },
            follow_redirects=False,
        )
        assert "error=invalid_team" in response.headers["location"]

    def test_unknown_scorer_rejected(self, client, participant):
        set_deadline_future()
        response = client.post(
            f"/p/{participant['token']}/pre-tournoi",
            data={
                "winner": "",
                "finalist": "",
                "top_scorer": "Joueur Inconnu",
                "revelation": "",
                "total_goals": "0",
                "action": "draft",
            },
            follow_redirects=False,
        )
        assert "error=invalid_scorer" in response.headers["location"]


class TestAdminAnswers:
    def test_guard_same_winner_finalist(self, admin_client):
        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={"winner": "Espagne", "finalist": "Espagne"},
            follow_redirects=False,
        )
        assert response.status_code == 303

        async def _answers():
            async with get_db() as db:
                rows = await db.execute(
                    "SELECT key, correct_answer FROM pre_tournament_questions"
                )
                return {r["key"]: r["correct_answer"] for r in await rows.fetchall()}

        answers = run(_answers())
        assert answers["winner"] != "Espagne"

    def test_answers_scored_into_rankings(self, admin_client, participant):
        set_deadline_future()
        scorer = get_scorer_choices()[0]
        # Participant submits a full prediction
        admin_client.post(
            f"/p/{participant['token']}/pre-tournoi",
            data={
                "winner": "Argentine",
                "finalist": "France",
                "top_scorer": scorer,
                "revelation": "Maroc",
                "total_goals": "150",
                "action": "submit",
            },
            follow_redirects=False,
        )
        # Admin encodes answers: winner + scorer correct, finalist/revelation wrong, goals within ±3
        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={
                "winner": "Argentine",
                "finalist": "Angleterre",
                "top_scorer": scorer,
                "revelation": "Japon",
                "total_goals": "152",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        rankings = run(get_rankings())
        me = next(r for r in rankings if r["id"] == participant["id"])
        # winner 8 + scorer 5 + goals near 4 = 17
        assert me["total_points"] == 17
