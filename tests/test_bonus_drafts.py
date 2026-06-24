from html import unescape
import uuid

from app.database import ensure_bonus_question_drafts, get_db
from tests.conftest import run


J3_GOALS = "Feu d'artifice J3 - Combien de buts seront marqués sur les 24 matchs de la troisième journée des groupes ?"
J3_POPCORN = "Match popcorn J3 - Y aura-t-il au moins un match avec 5 buts ou plus pendant la troisième journée des groupes ?"


def _bonus_question_by_text(text):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM bonus_questions WHERE question_text=?", (text,)
            )
            item = await row.fetchone()
            return dict(item) if item else None

    return run(_get())


def _ensure_j3_drafts():
    async def _ensure():
        async with get_db() as db:
            await db.execute(
                "DELETE FROM app_settings WHERE key='bonus_drafts_group_j3_2026_v1'"
            )
            await ensure_bonus_question_drafts(db)
            await db.commit()

    run(_ensure())


def _delete_bonus(question_id):
    async def _delete():
        async with get_db() as db:
            await db.execute("DELETE FROM bonus_questions WHERE id=?", (question_id,))
            await db.commit()

    run(_delete())


def test_seeded_j3_bonus_drafts_visible_admin_hidden_participant(admin_client, participant):
    _ensure_j3_drafts()
    admin_html = unescape(admin_client.get("/admin/bonus").text)
    participant_html = unescape(admin_client.get(f"/p/{participant['token']}/bonus").text)

    assert J3_GOALS in admin_html
    assert J3_POPCORN in admin_html
    assert "Brouillon" in admin_html
    assert J3_GOALS not in participant_html
    assert J3_POPCORN not in participant_html

    goals = _bonus_question_by_text(J3_GOALS)
    assert goals["phase"] == "group"
    assert goals["answer_type"] == "number"
    assert goals["scoring_mode"] == "closest_podium"
    assert goals["is_published"] == 0


def test_admin_can_prepare_draft_then_publish(admin_client):
    title = f"Question brouillon {uuid.uuid4()}"
    response = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": title,
            "phase": "group",
            "answer_type": "choice",
            "points_value": "6",
            "options_text": "Oui\nNon",
            "deadline": "2035-01-01T12:00",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    question = _bonus_question_by_text(title)
    assert question["is_published"] == 0

    try:
        response = admin_client.post(
            f"/admin/bonus/{question['id']}/update",
            data={
                "question_text": title,
                "phase": "group",
                "answer_type": "choice",
                "points_value": "6",
                "options_text": "Oui\nNon",
                "deadline": "2035-01-01T12:00",
                "is_published": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        question = _bonus_question_by_text(title)
        assert question["is_published"] == 1
    finally:
        _delete_bonus(question["id"])


def test_unpublished_bonus_cannot_be_submitted_directly(admin_client, participant):
    _ensure_j3_drafts()
    draft = _bonus_question_by_text(J3_POPCORN)

    response = admin_client.post(
        f"/p/{participant['token']}/bonus/{draft['id']}",
        data={"answer": "Oui"},
        follow_redirects=False,
    )

    assert response.status_code == 404
