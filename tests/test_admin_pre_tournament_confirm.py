"""Confirmation enrichie sur le formulaire des réponses correctes pré-tournoi
(/admin/pre-tournoi/reponses) : le recalcul de recalculate_pre_tournament_scores
impacte tous les participants ayant soumis, donc le nombre affiché doit
refléter exactement submitted_count.
"""
from app.database import get_db
from tests.conftest import run

_ACTION = 'action="/admin/pre-tournoi/reponses"'


def _extract_form(html):
    action_idx = html.index(_ACTION)
    form_start = html.rindex("<form", 0, action_idx)
    form_end = html.index("</form>", action_idx)
    return html[form_start:form_end]


def _seed_submitted_prediction(participant_id):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO pre_tournament_predictions (participant_id, submitted)
                   VALUES (?, 1)""",
                (participant_id,),
            )
            await db.commit()

    run(_create())


def _cleanup_prediction(participant_id):
    async def _clean():
        async with get_db() as db:
            await db.execute(
                "DELETE FROM pre_tournament_predictions WHERE participant_id=?",
                (participant_id,),
            )
            await db.commit()

    run(_clean())


def _clear_all_predictions():
    async def _clear():
        async with get_db() as db:
            await db.execute("DELETE FROM pre_tournament_predictions")
            await db.commit()

    run(_clear())


def _count_submitted():
    async def _count():
        async with get_db() as db:
            row = await db.execute(
                "SELECT COUNT(*) as cnt FROM pre_tournament_predictions WHERE submitted=1"
            )
            return (await row.fetchone())["cnt"]

    return run(_count())


def test_pre_tournament_reponses_form_confirm_reflects_submitted_count(admin_client, participant):
    _seed_submitted_prediction(participant["id"])
    try:
        expected_count = _count_submitted()
        assert expected_count > 0

        html = admin_client.get("/admin/pre-tournoi").text
        form_html = _extract_form(html)

        assert 'data-confirm-title="Recalculer les points pré-tournoi ?"' in form_html
        assert (
            "data-confirm=\"Cet enregistrement recalculera les points pré-tournoi de "
            f'{expected_count} participant(s) ayant soumis leurs réponses."' in form_html
        )
        assert "data-confirm-danger" in form_html
        assert "data-confirm-strong" not in form_html
    finally:
        _cleanup_prediction(participant["id"])


def test_pre_tournament_reponses_form_has_no_confirm_without_submissions(admin_client):
    # Réinitialise la table pour garantir submitted_count = 0, sans dépendre de
    # l'ordre d'exécution des autres fichiers de tests (même pattern que
    # clear_operational_data() dans test_admin_communications_push.py).
    _clear_all_predictions()
    assert _count_submitted() == 0

    html = admin_client.get("/admin/pre-tournoi").text
    form_html = _extract_form(html)

    assert "data-confirm" not in form_html
