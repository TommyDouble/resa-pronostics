"""Admin: encoder pré-tournoi et bonus au nom d'un participant (deadline passée incluse)."""
import uuid

from app.database import get_db
from tests.conftest import run


def _make_participant(name):
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


def _pt_row(pid):
    async def _q():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM pre_tournament_predictions WHERE participant_id=?", (pid,)
            )
            r = await row.fetchone()
            return dict(r) if r else None

    return run(_q())


def _make_bonus_question(answer_type="choice", options='["Oui", "Non"]',
                         correct_answer=None, points_value=5):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value,
                    correct_answer, deadline)
                   VALUES ('Question test ?', 'round_of_16', ?, ?, ?, ?, '2000-01-01T00:00:00')""",
                (answer_type, options, points_value, correct_answer),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _bonus_answer(pid, qid):
    async def _q():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM bonus_answers WHERE participant_id=? AND question_id=?",
                (pid, qid),
            )
            ans = await row.fetchone()
            score_row = await db.execute(
                "SELECT points FROM scores WHERE participant_id=? AND bonus_question_id=?",
                (pid, qid),
            )
            score = await score_row.fetchone()
            return (dict(ans) if ans else None, score["points"] if score else None)

    return run(_q())


# ---- Pré-tournoi ----

def test_force_pt_creates_row_with_partial_fields(admin_client):
    pid = _make_participant("PT Force Création")
    admin_client.post(
        "/admin/pronostics/force-pre-tournoi",
        data={"participant_id": pid, "winner": "France", "total_goals": "150"},
        follow_redirects=False,
    )
    row = _pt_row(pid)
    assert row["winner"] == "France"
    assert row["total_goals"] == 150
    assert row["finalist"] is None
    assert row["submitted"] == 1
    assert row["admin_entered"] == 1


def test_force_pt_partial_update_preserves_existing(admin_client):
    pid = _make_participant("PT Force Partiel")
    admin_client.post(
        "/admin/pronostics/force-pre-tournoi",
        data={"participant_id": pid, "winner": "France", "finalist": "Brésil"},
        follow_redirects=False,
    )
    admin_client.post(
        "/admin/pronostics/force-pre-tournoi",
        data={"participant_id": pid, "revelation": "Maroc"},
        follow_redirects=False,
    )
    row = _pt_row(pid)
    assert row["winner"] == "France"
    assert row["finalist"] == "Brésil"
    assert row["revelation"] == "Maroc"


def test_force_pt_rejects_winner_equals_finalist(admin_client):
    pid = _make_participant("PT Force Conflit")
    admin_client.post(
        "/admin/pronostics/force-pre-tournoi",
        data={"participant_id": pid, "winner": "France"},
        follow_redirects=False,
    )
    admin_client.post(
        "/admin/pronostics/force-pre-tournoi",
        data={"participant_id": pid, "finalist": "France"},
        follow_redirects=False,
    )
    row = _pt_row(pid)
    assert row["finalist"] is None  # refusé : identique au champion existant


def test_force_pt_rejects_invalid_values(admin_client):
    pid = _make_participant("PT Force Invalide")
    for data in (
        {"participant_id": pid, "winner": "Atlantide"},
        {"participant_id": pid, "revelation": "France"},  # pas un outsider
        {"participant_id": pid, "total_goals": "beaucoup"},
        {"participant_id": pid},  # aucun champ
    ):
        admin_client.post(
            "/admin/pronostics/force-pre-tournoi", data=data, follow_redirects=False
        )
    assert _pt_row(pid) is None


# ---- Bonus ----

def test_force_bonus_past_deadline_and_recalculates(admin_client):
    pid = _make_participant("Bonus Force")
    qid = _make_bonus_question(correct_answer="Oui", points_value=5)
    admin_client.post(
        "/admin/pronostics/force-bonus",
        data={"participant_id": pid, "question_id": qid, "answer": "oui"},
        follow_redirects=False,
    )
    ans, points = _bonus_answer(pid, qid)
    assert ans["answer"] == "Oui"  # casse canonique de l'option
    assert ans["admin_entered"] == 1
    assert points == 5


def test_force_bonus_rejects_unknown_choice(admin_client):
    pid = _make_participant("Bonus Force Invalide")
    qid = _make_bonus_question()
    admin_client.post(
        "/admin/pronostics/force-bonus",
        data={"participant_id": pid, "question_id": qid, "answer": "Peut-être"},
        follow_redirects=False,
    )
    ans, _ = _bonus_answer(pid, qid)
    assert ans is None


def test_force_bonus_overwrites_existing_answer(admin_client):
    pid = _make_participant("Bonus Force Écrase")
    qid = _make_bonus_question()

    async def _seed():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO bonus_answers (participant_id, question_id, answer) VALUES (?,?,'Non')",
                (pid, qid),
            )
            await db.commit()

    run(_seed())
    admin_client.post(
        "/admin/pronostics/force-bonus",
        data={"participant_id": pid, "question_id": qid, "answer": "Oui"},
        follow_redirects=False,
    )
    ans, _ = _bonus_answer(pid, qid)
    assert ans["answer"] == "Oui"
    assert ans["admin_entered"] == 1
