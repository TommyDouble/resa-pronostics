"""Confirmation enrichie sur le formulaire de mise à jour d'une question bonus
(/admin/bonus/{id}/update) : présente quand des réponses existent (le
recalcul de calculate_bonus_scores impacte des participants), absente sinon.
"""
from app.database import get_db
from tests.conftest import run

_QUESTION_TEXT_WITH_ANSWER = "Quelle équipe soulèvera le trophée ? (confirm-test-avec-reponse)"
_QUESTION_TEXT_NO_ANSWER = "Quelle équipe soulèvera le trophée ? (confirm-test-sans-reponse)"


def _extract_form(html, question_id):
    action = f'action="/admin/bonus/{question_id}/update"'
    action_idx = html.index(action)
    form_start = html.rindex("<form", 0, action_idx)
    form_end = html.index("</form>", action_idx)
    return html[form_start:form_end]


def _seed_question(question_text):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value, deadline)
                   VALUES (?, 'pre_tournament', 'choice', ?, 5, '2020-01-01T12:00:00')""",
                (question_text, '["France","Brésil"]'),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _seed_answer(question_id, participant_id):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO bonus_answers (participant_id, question_id, answer)
                   VALUES (?, ?, 'France')""",
                (participant_id, question_id),
            )
            await db.commit()

    run(_create())


def _cleanup(question_ids):
    async def _clean():
        async with get_db() as db:
            placeholders = ",".join("?" for _ in question_ids)
            await db.execute(
                f"DELETE FROM bonus_questions WHERE id IN ({placeholders})", question_ids
            )
            await db.commit()

    run(_clean())


def test_bonus_update_form_has_confirm_when_answers_exist(admin_client, participant):
    question_id = _seed_question(_QUESTION_TEXT_WITH_ANSWER)
    try:
        _seed_answer(question_id, participant["id"])

        html = admin_client.get("/admin/bonus").text
        form_html = _extract_form(html, question_id)

        assert 'data-confirm-title="Enregistrer et recalculer ?"' in form_html
        assert (
            'data-confirm="Cette mise à jour recalculera les points de 1 participant(s) '
            'ayant répondu à cette question."' in form_html
        )
        assert "data-confirm-danger" in form_html
        assert "data-confirm-strong" not in form_html
    finally:
        _cleanup([question_id])


def test_bonus_update_form_has_no_confirm_without_answers(admin_client):
    question_id = _seed_question(_QUESTION_TEXT_NO_ANSWER)
    try:
        html = admin_client.get("/admin/bonus").text
        form_html = _extract_form(html, question_id)

        assert "data-confirm" not in form_html
    finally:
        _cleanup([question_id])
