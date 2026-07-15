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
    def test_guard_same_finalists(self, admin_client):
        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={"finalist_1": "Espagne", "finalist_2": "Espagne"},
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
        assert answers["finalist"] != '["Espagne", "Espagne"]'

    def test_finalists_can_be_scored_before_winner(self, admin_client, participant):
        set_deadline_future()
        admin_client.post(
            f"/p/{participant['token']}/pre-tournoi",
            data={
                "winner": "Argentine",
                "finalist": "France",
                "top_scorer": "",
                "revelation": "",
                "total_goals": "0",
            },
            follow_redirects=False,
        )

        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={
                "winner": "",
                "finalist_1": "Argentine",
                "finalist_2": "France",
                "top_scorer": "",
                "revelation": "",
                "total_goals": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        rankings = run(get_rankings())
        me = next(r for r in rankings if r["id"] == participant["id"])
        assert me["total_points"] == 14

        async def _scores():
            async with get_db() as db:
                rows = await db.execute(
                    "SELECT question_key, points FROM pre_tournament_scores WHERE participant_id=?",
                    (participant["id"],),
                )
                return {r["question_key"]: r["points"] for r in await rows.fetchall()}

        scores = run(_scores())
        assert scores["finalist"] == 14
        assert "winner" not in scores

        admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={
                "winner": "Argentine",
                "finalist_1": "Argentine",
                "finalist_2": "France",
                "top_scorer": "",
                "revelation": "",
                "total_goals": "",
            },
            follow_redirects=False,
        )
        rankings = run(get_rankings())
        me = next(r for r in rankings if r["id"] == participant["id"])
        assert me["total_points"] == 22

    def test_winner_must_be_one_of_saved_finalists(self, admin_client):
        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={
                "winner": "Brésil",
                "finalist_1": "Argentine",
                "finalist_2": "France",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        async def _winner_answer():
            async with get_db() as db:
                row = await db.execute(
                    "SELECT correct_answer FROM pre_tournament_questions WHERE key='winner'"
                )
                return (await row.fetchone())["correct_answer"]

        assert run(_winner_answer()) != "Brésil"

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
        # Admin encodes answers: champion + both finalists + scorer correct, revelation wrong, goals within ±3
        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={
                "winner": "Argentine",
                "finalist_1": "Argentine",
                "finalist_2": "France",
                "top_scorer": scorer,
                "revelation": "Japon",
                "total_goals": "152",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        rankings = run(get_rankings())
        me = next(r for r in rankings if r["id"] == participant["id"])
        # finalists 14 + champion 8 + scorer 8 + goals near 4 = 34
        assert me["total_points"] == 34

    def test_any_saved_answer_scores_no_draft_trap(self, admin_client, participant):
        """Une sauvegarde simple (ancien « brouillon ») compte pour les points."""
        set_deadline_future()
        admin_client.post(
            f"/p/{participant['token']}/pre-tournoi",
            data={
                "winner": "Argentine",
                "finalist": "",
                "top_scorer": "",
                "revelation": "",
                "total_goals": "0",
            },
            follow_redirects=False,
        )
        row = get_pt_row(participant["id"])
        assert row["submitted"] == 1
        admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={"winner": "Argentine", "finalist_1": "", "finalist_2": "", "top_scorer": "",
                  "revelation": "", "total_goals": ""},
            follow_redirects=False,
        )
        rankings = run(get_rankings())
        me = next(r for r in rankings if r["id"] == participant["id"])
        # Champion correct (+8). Les points finalistes tomberont quand la
        # réponse « finalist » sera encodée à son tour.
        assert me["total_points"] == 8

    def test_inverted_finalists_still_score_finalist_points(self, admin_client, participant):
        set_deadline_future()
        admin_client.post(
            f"/p/{participant['token']}/pre-tournoi",
            data={
                "winner": "France",
                "finalist": "Argentine",
                "top_scorer": "",
                "revelation": "",
                "total_goals": "140",
                "action": "submit",
            },
            follow_redirects=False,
        )
        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={
                "winner": "Argentine",
                "finalist_1": "Argentine",
                "finalist_2": "France",
                "top_scorer": "",
                "revelation": "",
                "total_goals": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        rankings = run(get_rankings())
        me = next(r for r in rankings if r["id"] == participant["id"])
        # The two finalists are right (+14), but champion is wrong (+0).
        assert me["total_points"] == 14


def _make_participant():
    """Create a confirmed participant directly and return {id, token}."""
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, first_name, last_name, email, token, is_confirmed)
                   VALUES (?,?,?,?,?,1)""",
                ("Outsider Fan", "Outsider", "Fan", f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid

    return {"id": run(_create()), "token": token}


class TestHomeCta:
    def test_single_pretournoi_cta_during_onboarding(self, client, participant):
        # Fresh participant, pré-tournoi open and not submitted: the home page must
        # show exactly one call-to-action toward the pré-tournoi (no duplicate box).
        set_deadline_future()
        response = client.get(f"/p/{participant['token']}")
        assert response.status_code == 200
        href = f"/p/{participant['token']}/pre-tournoi"
        assert response.text.count(href) == 1


class TestRevelationMultipleWinners:
    def test_two_winning_outsiders_both_score(self, admin_client, participant):
        set_deadline_future()
        second = _make_participant()
        # Participant 1 picks Maroc, participant 2 picks Japon.
        for token, outsider in ((participant["token"], "Maroc"), (second["token"], "Japon")):
            admin_client.post(
                f"/p/{token}/pre-tournoi",
                data={
                    "winner": "",
                    "finalist": "",
                    "top_scorer": "",
                    "revelation": outsider,
                    "total_goals": "0",
                    "action": "submit",
                },
                follow_redirects=False,
            )
        # Admin declares BOTH Maroc and Japon winning outsiders (tie).
        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={
                "winner": "",
                "finalist": "",
                "top_scorer": "",
                "revelation": ["Maroc", "Japon"],
                "total_goals": "",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        rankings = run(get_rankings())
        p1 = next(r for r in rankings if r["id"] == participant["id"])
        p2 = next(r for r in rankings if r["id"] == second["id"])
        assert p1["total_points"] == 5
        assert p2["total_points"] == 5

    def test_non_outsider_winner_rejected(self, admin_client):
        response = admin_client.post(
            "/admin/pre-tournoi/reponses",
            data={"revelation": ["France"]},
            follow_redirects=False,
        )
        assert response.status_code == 303

        async def _answer():
            async with get_db() as db:
                row = await db.execute(
                    "SELECT correct_answer FROM pre_tournament_questions WHERE key='revelation'"
                )
                r = await row.fetchone()
                return r["correct_answer"]

        # France is not an outsider → rejected, answer not stored as France.
        assert "France" not in (run(_answer()) or "")
