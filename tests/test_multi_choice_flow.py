import json

from app.database import get_db
from conftest import run


def _fetch_question_by_text(text_like):
    async def _q():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM bonus_questions WHERE question_text LIKE ?",
                (f"%{text_like}%",),
            )
            r = await row.fetchone()
            return dict(r) if r else None
    return run(_q())


def test_round_of_32_drafts_seeded(client):
    # The migration seeds the three "seizièmes" questions as drafts.
    afrique = _fetch_question_by_text("Afrique Mode Patron")
    assert afrique is not None
    assert afrique["answer_type"] == "number_multi"
    assert afrique["scoring_mode"] == "number_multi"
    assert afrique["is_published"] == 0
    options = json.loads(afrique["options"])
    assert "Sénégal" in options and len(options) == 7
    # The two obsolete v1 Afrique questions must be gone.
    assert _fetch_question_by_text("Afrique Mode Patron (expert)") is None

    favori = _fetch_question_by_text("Le Favori Qui Tremble")
    assert favori is not None
    assert favori["help_text"] and "but contre son camp" in favori["help_text"]

    tab = _fetch_question_by_text("Encore des tirs au but")
    assert tab is not None
    assert json.loads(tab["scoring_config"])["rank_points"] == [3, 2, 1]


def test_multi_choice_end_to_end(client, admin_client, participant):
    # Create a published multi_choice question via the admin form.
    resp = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": "Test multi quelles équipes",
            "phase": "round_of_32",
            "answer_type": "multi_choice",
            "points_value": "6",
            "deadline": "2030-01-01T12:00",
            "options_text": "Alpha\nBeta\nGamma",
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    q = _fetch_question_by_text("Test multi quelles équipes")
    assert q is not None and q["scoring_mode"] == "multi_select"
    qid = q["id"]

    # Participant checks two boxes (multiple "answer" values).
    resp = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"answer": ["Alpha", "Beta"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    stored = run(_stored_answer(participant["id"], qid))
    assert set(json.loads(stored)) == {"Alpha", "Beta"}

    # Admin sets the correct answer (Alpha + Gamma) via update → 2 errors → 2 pts.
    resp = admin_client.post(
        f"/admin/bonus/{qid}/update",
        data={
            "question_text": "Test multi quelles équipes",
            "phase": "round_of_32",
            "answer_type": "multi_choice",
            "points_value": "6",
            "deadline": "2030-01-01T12:00",
            "options_text": "Alpha\nBeta\nGamma",
            "correct_answer": ["Alpha", "Gamma"],
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    points = run(_score(participant["id"], qid))
    assert points == 2


def test_number_multi_end_to_end(client, admin_client, participant):
    resp = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": "Test combo nombre et équipes",
            "phase": "round_of_32",
            "answer_type": "number_multi",
            "points_value": "3",
            "deadline": "2030-01-01T12:00",
            "options_text": "Alpha\nBeta\nGamma\nDelta",
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    q = _fetch_question_by_text("Test combo nombre et équipes")
    assert q is not None and q["scoring_mode"] == "number_multi"
    qid = q["id"]

    # Participant: total = 3 (so must pick exactly 2 teams).
    resp = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"count": "3", "teams": ["Alpha", "Beta"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    stored = json.loads(run(_stored_answer(participant["id"], qid)))
    assert stored["count"] == 3 and set(stored["teams"]) == {"Alpha", "Beta"}

    # Wrong team count is rejected (must equal total - 1 = 2).
    bad = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"count": "3", "teams": ["Alpha"]},
        follow_redirects=False,
    )
    assert bad.status_code == 400

    # Admin correct answer: total 3, teams Alpha + Gamma.
    # Participant: count exact (3) → +3 ; Alpha right (+1), Beta wrong (-1) → +0 ; total 3.
    resp = admin_client.post(
        f"/admin/bonus/{qid}/update",
        data={
            "question_text": "Test combo nombre et équipes",
            "phase": "round_of_32",
            "answer_type": "number_multi",
            "points_value": "3",
            "deadline": "2030-01-01T12:00",
            "options_text": "Alpha\nBeta\nGamma\nDelta",
            "correct_count": "3",
            "correct_answer": ["Alpha", "Gamma"],
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert run(_score(participant["id"], qid)) == 3


async def _stored_answer(pid, qid):
    async with get_db() as db:
        row = await db.execute(
            "SELECT answer FROM bonus_answers WHERE participant_id=? AND question_id=?",
            (pid, qid),
        )
        r = await row.fetchone()
        return r["answer"] if r else None


async def _score(pid, qid):
    async with get_db() as db:
        row = await db.execute(
            "SELECT points FROM scores WHERE participant_id=? AND bonus_question_id=?",
            (pid, qid),
        )
        r = await row.fetchone()
        return r["points"] if r else None
