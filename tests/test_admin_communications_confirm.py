"""Confirmations d'impact sur les envois de /admin/communications (rappels, push de
test) : chaque formulaire de send doit exposer un data-confirm avec un compteur qui
correspond exactement au filtre de destinataires réellement utilisé par la route.
"""
from app.database import get_db
from tests.conftest import run
from tests.test_admin_communications_push import (
    add_subscription,
    clear_operational_data,
    create_match,
    create_participant,
    mark_pre_tournament_submitted,
)


def _extract_form(html, action):
    action_str = f'action="{action}"'
    action_idx = html.index(action_str)
    form_start = html.rindex("<form", 0, action_idx)
    form_end = html.index("</form>", action_idx)
    return html[form_start:form_end]


def _set_email_opt_in(participant_id, value):
    async def _set():
        async with get_db() as db:
            await db.execute(
                "UPDATE participants SET email_opt_in=? WHERE id=?",
                (value, participant_id),
            )
            await db.commit()

    run(_set())


def test_pt_reminder_confirm_reflects_email_opt_in_filtered_count(admin_client):
    clear_operational_data()
    target = create_participant(name="PT Opt In")
    opted_out = create_participant(name="PT Opt Out")
    _set_email_opt_in(opted_out["id"], 0)
    submitted = create_participant(name="PT Already Submitted")
    mark_pre_tournament_submitted(submitted["id"])

    html = admin_client.get("/admin/communications").text
    form_html = _extract_form(html, "/admin/communications/send-pt-reminder")

    assert 'data-confirm-title="Envoyer le rappel pré-tournoi ?"' in form_html
    assert (
        "data-confirm=\"Ce rappel sera envoyé par email à 1 participant(s) "
        'n\'ayant pas encore soumis leurs réponses pré-tournoi."' in form_html
    )
    assert "data-confirm-danger" not in form_html
    assert target["id"]  # sanity: the one counted participant was actually created


def test_pt_reminder_no_confirm_when_zero(admin_client):
    clear_operational_data()

    html = admin_client.get("/admin/communications").text
    form_html = _extract_form(html, "/admin/communications/send-pt-reminder")

    assert "data-confirm" not in form_html
    assert "disabled" in form_html


def test_match_reminder_confirm_present_when_match_eligible(admin_client):
    clear_operational_data()
    create_match(970401, kickoff="2035-04-01T12:00:00", team1="Espagne", team2="Portugal")

    html = admin_client.get("/admin/communications").text
    form_html = _extract_form(html, "/admin/communications/send-match-reminder")

    assert 'data-confirm-title="Envoyer le rappel de match ?"' in form_html
    assert (
        'data-confirm="Ce rappel sera envoyé par email aux participants '
        'n\'ayant pas encore pronostiqué ce match."' in form_html
    )
    assert "data-confirm-danger" not in form_html


def test_match_reminder_no_confirm_when_no_eligible_match(admin_client):
    clear_operational_data()

    html = admin_client.get("/admin/communications").text
    form_html = _extract_form(html, "/admin/communications/send-match-reminder")

    assert "data-confirm" not in form_html
    assert "disabled" in form_html


def test_deadline_send_form_confirm_reflects_preview_participant_count(
    admin_client, monkeypatch
):
    clear_operational_data()
    reachable = create_participant(name="Confirm Reachable")
    unreachable = create_participant(name="Confirm Unreachable")
    add_subscription(reachable["id"])
    mark_pre_tournament_submitted(reachable["id"])
    mark_pre_tournament_submitted(unreachable["id"])
    create_match(970501, kickoff="2035-05-01T12:00:00", team1="Argentine", team2="Uruguay")

    monkeypatch.setattr("app.routers.admin.push_enabled", lambda: True)

    preview = admin_client.post(
        "/admin/communications/deadline-reminders/preview",
        data={"deadline_keys": ["2035-05-01T12:00:00"]},
    )

    assert preview.status_code == 200
    form_html = _extract_form(preview.text, "/admin/communications/deadline-reminders/send")

    assert 'data-confirm-title="Envoyer le rappel groupé ?"' in form_html
    assert (
        'data-confirm="Ce rappel ciblera 2 participant(s) concerné(s) par les '
        'deadlines sélectionnées."' in form_html
    )
    assert "data-confirm-danger" in form_html


def test_deadline_send_form_confirm_mentions_email_when_forced(admin_client, monkeypatch):
    clear_operational_data()
    participant = create_participant(name="Confirm Force Email")
    mark_pre_tournament_submitted(participant["id"])
    create_match(970502, kickoff="2035-05-02T12:00:00", team1="Chili", team2="Colombie")

    monkeypatch.setattr("app.routers.admin.push_enabled", lambda: True)

    preview = admin_client.post(
        "/admin/communications/deadline-reminders/preview",
        data={"deadline_keys": ["2035-05-02T12:00:00"], "force_email": "1"},
    )

    assert preview.status_code == 200
    form_html = _extract_form(preview.text, "/admin/communications/deadline-reminders/send")

    assert (
        "L'option email forcé est active : un email pourra aussi être envoyé "
        "aux participants concernés disposant d'une adresse email." in form_html
    )


def test_push_test_form_has_static_initial_confirm_and_danger(admin_client):
    clear_operational_data()

    html = admin_client.get("/admin/communications").text
    form_html = _extract_form(html, "/admin/communications/send-push-test")

    assert 'data-confirm-title="Envoyer cette notification push ?"' in form_html
    assert (
        'data-confirm="Ce push de test sera envoyé à 0 destinataire(s) '
        'sélectionné(s)."' in form_html
    )
    assert "data-confirm-danger" in form_html


def test_push_test_form_js_hooks_unchanged(admin_client):
    clear_operational_data()
    first = create_participant(name="Hook First")
    second = create_participant(name="Hook Second")

    html = admin_client.get("/admin/communications").text
    form_html = _extract_form(html, "/admin/communications/send-push-test")

    assert "data-push-target-all" in form_html
    assert form_html.count("data-push-recipient") == 2
    assert f'value="{first["id"]}"' in form_html
    assert f'value="{second["id"]}"' in form_html
