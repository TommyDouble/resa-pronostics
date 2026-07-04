"""Admin communications: manual targeted push tests."""
from html import unescape
import uuid

import app.routers.admin as admin_routes
from app.config import settings
from app.database import (
    ensure_bonus_question_drafts,
    ensure_round_of_16_bonus_drafts,
    ensure_round_of_32_bonus_drafts,
    get_db,
)
from tests.conftest import run


def create_participant(*, name="Push User", confirmed=1, is_admin=0):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants
                   (name, first_name, last_name, email, token, is_confirmed, is_admin)
                   VALUES (?,?,?,?,?,?,?)""",
                (name, name.split()[0], "User", f"{token}@test.local", token, confirmed, is_admin),
            )
            await db.commit()
            return {"id": cursor.lastrowid, "token": token, "email": f"{token}@test.local"}

    return run(_create())


def add_subscription(participant_id):
    async def _add():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO push_subscriptions (participant_id, endpoint, p256dh, auth)
                   VALUES (?,?,?,?)""",
                (
                    participant_id,
                    f"https://push.example.com/{uuid.uuid4().hex}",
                    f"p256dh-{uuid.uuid4().hex}",
                    f"auth-{uuid.uuid4().hex}",
                ),
            )
            await db.commit()

    run(_add())


def notification_log_count():
    async def _count():
        async with get_db() as db:
            row = await db.execute("SELECT COUNT(*) AS cnt FROM notification_log")
            return (await row.fetchone())["cnt"]

    return run(_count())


def clear_operational_data():
    async def _clear():
        async with get_db() as db:
            for table in (
                "notification_log",
                "push_subscriptions",
                "scores",
                "predictions",
                "bonus_answers",
                "bonus_questions",
                "matches",
                "pre_tournament_predictions",
                "participants",
            ):
                await db.execute(f"DELETE FROM {table}")
            await db.execute(
                """DELETE FROM app_settings
                   WHERE key IN (
                     'pre_tournament_deadline',
                     'bonus_drafts_group_j3_2026_v1',
                     'bonus_drafts_round_of_32_2026_v4',
                     'bonus_drafts_round_of_16_2026_v1'
                   )"""
            )
            await ensure_bonus_question_drafts(db)
            await ensure_round_of_32_bonus_drafts(db)
            await ensure_round_of_16_bonus_drafts(db)
            await db.commit()

    run(_clear())


def mark_pre_tournament_submitted(participant_id):
    async def _mark():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO pre_tournament_predictions (participant_id, submitted)
                   VALUES (?, 1)
                   ON CONFLICT(participant_id) DO UPDATE SET submitted=1""",
                (participant_id,),
            )
            await db.commit()

    run(_mark())


def create_match(number, *, kickoff="2035-01-01T12:00:00", team1="France", team2="Brésil"):
    date, time = kickoff.split("T")

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time, team1_name, team2_name, weight)
                   VALUES (?, 'group', ?, ?, ?, ?, 1)""",
                (number, date, time, team1, team2),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def create_bonus_question(text, *, deadline="2035-01-01T12:00:00"):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value,
                    scoring_mode, is_published, deadline)
                   VALUES (?, 'group', 'choice', '["Oui","Non"]', 6, 'exact', 1, ?)""",
                (text, deadline),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def add_prediction(participant_id, match_id):
    async def _add():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions
                   (participant_id, match_id, prediction, exact_score_team1, exact_score_team2)
                   VALUES (?, ?, 'team1', 2, 1)""",
                (participant_id, match_id),
            )
            await db.commit()

    run(_add())


def test_communications_shows_manual_push_card(admin_client, participant, monkeypatch):
    add_subscription(participant["id"])
    monkeypatch.setattr(admin_routes, "push_enabled", lambda: True)

    response = admin_client.get("/admin/communications")

    assert response.status_code == 200
    assert "Notification push de test" in response.text
    assert f"{participant['token']}@test.local" in response.text
    assert "1 appareil" in response.text
    assert 'name="target_mode" value="all"' in response.text
    assert 'type="checkbox" name="participant_ids"' in response.text
    assert "Envoyer la notification push" in response.text


def test_push_test_refuses_without_vapid(admin_client, participant, monkeypatch):
    monkeypatch.setattr(admin_routes, "push_enabled", lambda: False)

    response = admin_client.post(
        "/admin/communications/send-push-test",
        data={
            "participant_ids": [str(participant["id"])],
            "title": "Test",
            "body": "Message",
            "destination": "home",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Notifications push non configurées" in response.text


def test_push_test_requires_recipient_and_content(admin_client, participant, monkeypatch):
    monkeypatch.setattr(admin_routes, "push_enabled", lambda: True)

    no_recipient = admin_client.post(
        "/admin/communications/send-push-test",
        data={"title": "Test", "body": "Message", "destination": "home"},
        follow_redirects=True,
    )
    assert "Sélectionne au moins un participant" in no_recipient.text

    no_content = admin_client.post(
        "/admin/communications/send-push-test",
        data={
            "participant_ids": [str(participant["id"])],
            "title": "",
            "body": " ",
            "destination": "home",
        },
        follow_redirects=True,
    )
    assert "Titre et message sont obligatoires" in no_content.text


def test_push_test_filters_targets_and_uses_personal_url(admin_client, participant, monkeypatch):
    admin_participant = create_participant(name="Admin Participant", is_admin=1)
    unconfirmed = create_participant(name="Unconfirmed Participant", confirmed=0)
    calls = []

    async def fake_send(db, participant_id, *, title, body, url):
        calls.append({"participant_id": participant_id, "title": title, "body": body, "url": url})
        return True

    monkeypatch.setattr(admin_routes, "push_enabled", lambda: True)
    monkeypatch.setattr(admin_routes, "send_push_to_participant", fake_send)

    response = admin_client.post(
        "/admin/communications/send-push-test",
        data={
            "participant_ids": [
                str(participant["id"]),
                str(admin_participant["id"]),
                str(unconfirmed["id"]),
                "999999",
            ],
            "title": "Titre de test",
            "body": "Message de test",
            "destination": "pronos",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "1/1 notification(s) push envoyée(s)" in response.text
    assert calls == [{
        "participant_id": participant["id"],
        "title": "Titre de test",
        "body": "Message de test",
        "url": f"{settings.BASE_URL.rstrip('/')}/p/{participant['token']}/pronos",
    }]


def test_push_test_all_targets_confirmed_non_admins_without_notification_log(
    admin_client, participant, monkeypatch
):
    confirmed = create_participant(name="Global Push")
    create_participant(name="Admin Push", is_admin=1)
    create_participant(name="Pending Push", confirmed=0)
    before_logs = notification_log_count()
    calls = []

    async def fake_send(db, participant_id, *, title, body, url):
        calls.append({"participant_id": participant_id, "title": title, "body": body, "url": url})
        return True

    monkeypatch.setattr(admin_routes, "push_enabled", lambda: True)
    monkeypatch.setattr(admin_routes, "send_push_to_participant", fake_send)

    response = admin_client.post(
        "/admin/communications/send-push-test",
        data={
            "target_mode": "all",
            "title": "Bon tournoi",
            "body": "Les notifications globales fonctionnent.",
            "destination": "classement",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "notification(s) push envoyée(s)" in response.text
    call_ids = {call["participant_id"] for call in calls}
    assert participant["id"] in call_ids
    assert confirmed["id"] in call_ids
    assert all(call["title"] == "Bon tournoi" for call in calls)
    assert all(call["body"] == "Les notifications globales fonctionnent." for call in calls)
    assert {
        f"{settings.BASE_URL.rstrip('/')}/p/{participant['token']}/classement",
        f"{settings.BASE_URL.rstrip('/')}/p/{confirmed['token']}/classement",
    }.issubset({call["url"] for call in calls})
    assert notification_log_count() == before_logs


def test_communications_shows_grouped_encoding_deadlines(admin_client):
    clear_operational_data()
    first = create_participant(name="Deadline First")
    second = create_participant(name="Deadline Second")
    add_subscription(first["id"])
    mark_pre_tournament_submitted(first["id"])
    mark_pre_tournament_submitted(second["id"])

    future_match = create_match(
        970101,
        kickoff="2035-01-02T12:00:00",
        team1="France",
        team2="Japon",
    )
    create_bonus_question(
        "Bonus groupé communications — Question visible",
        deadline="2035-01-02T12:00:00",
    )
    create_match(
        970102,
        kickoff="2020-01-02T12:00:00",
        team1="Retard",
        team2="Passé",
    )
    add_prediction(first["id"], future_match)

    html = admin_client.get("/admin/communications").text

    assert "Deadlines d'encodage" in html
    assert "10 prochaines deadlines" in html
    assert "Forcer aussi l'envoi email" in html
    assert "Retards dépassés" in html
    assert "#970101 · France" in html
    assert "Bonus · Bonus groupé communications" in html
    assert 'value="2035-01-02T12:00:00"' in html
    assert "0/2 complet" in html
    assert "2 manquants" in html
    assert "1 joignable" in html
    assert "#970102 · Retard" in html
    assert "dépassée" in html


def test_grouped_deadline_reminder_preview_send_and_duplicate_notice(
    admin_client, monkeypatch
):
    clear_operational_data()
    reachable = create_participant(name="Reachable Deadline")
    unreachable = create_participant(name="Unreachable Deadline")
    create_participant(name="Admin Deadline", is_admin=1)
    create_participant(name="Pending Deadline", confirmed=0)
    add_subscription(reachable["id"])
    mark_pre_tournament_submitted(reachable["id"])
    mark_pre_tournament_submitted(unreachable["id"])
    create_match(970201, kickoff="2035-02-01T12:00:00", team1="Maroc", team2="Canada")

    monkeypatch.setattr(admin_routes, "push_enabled", lambda: True)

    preview = admin_client.post(
        "/admin/communications/deadline-reminders/preview",
        data={"deadline_keys": ["2035-02-01T12:00:00"]},
    )

    assert preview.status_code == 200
    assert "Confirmation rappel groupé" in preview.text
    assert "2 participant(s) impacté(s)" in preview.text
    assert "1 joignable(s) push" in preview.text
    assert "1 sans push actif" in preview.text
    assert "Reachable Deadline" in preview.text
    assert "Unreachable Deadline" in preview.text
    assert "Admin Deadline" not in preview.text
    assert "Pending Deadline" not in preview.text

    calls = []

    async def fake_send(db, participant_id, *, title, body, url):
        calls.append({"participant_id": participant_id, "title": title, "body": body, "url": url})
        return True

    monkeypatch.setattr(admin_routes, "send_push_to_participant", fake_send)

    sent = admin_client.post(
        "/admin/communications/deadline-reminders/send",
        data={"deadline_keys": ["2035-02-01T12:00:00"]},
        follow_redirects=True,
    )

    assert sent.status_code == 200
    assert "1/1 push envoyé(s)" in sent.text
    assert "1 participant(s) sans push actif" in sent.text
    assert calls == [{
        "participant_id": reachable["id"],
        "title": "RESA Pronostics - 1 info à encoder",
        "body": "À compléter: #970201 · Maroc – Canada",
        "url": f"{settings.BASE_URL.rstrip('/')}/p/{reachable['token']}",
    }]

    second = admin_client.post(
        "/admin/communications/deadline-reminders/send",
        data={"deadline_keys": ["2035-02-01T12:00:00"]},
        follow_redirects=True,
    )

    assert second.status_code == 200
    assert "1 participant(s) avaient déjà reçu un rappel aujourd'hui" in unescape(second.text)


def test_grouped_deadline_reminder_force_email_previews_and_sends_all_channels(
    admin_client, monkeypatch
):
    clear_operational_data()
    reachable = create_participant(name="Alpha Email")
    no_push = create_participant(name="Beta Email")
    add_subscription(reachable["id"])
    mark_pre_tournament_submitted(reachable["id"])
    mark_pre_tournament_submitted(no_push["id"])
    create_match(970301, kickoff="2035-03-01T12:00:00", team1="Italie", team2="Chili")

    monkeypatch.setattr(admin_routes, "push_enabled", lambda: True)

    preview = admin_client.post(
        "/admin/communications/deadline-reminders/preview",
        data={"deadline_keys": ["2035-03-01T12:00:00"], "force_email": "1"},
    )

    html = unescape(preview.text)
    assert preview.status_code == 200
    assert "email forcé" in html
    assert "2 email(s) forcé(s)" in html
    assert "Contenu prévisualisé" in html
    assert "Exemple personnalisé pour Alpha Email" in html
    assert "RESA Pronostics - 1 info à encoder" in html
    assert "Bonjour Alpha Email" in html
    assert "#970301 · Italie – Chili" in html
    assert 'name="force_email" value="1"' in html
    assert "Envoyer 1 push + 2 emails" in html

    push_calls = []
    email_calls = []

    async def fake_push(db, participant_id, *, title, body, url):
        push_calls.append({"participant_id": participant_id, "title": title, "body": body, "url": url})
        return True

    async def fake_email(to, subject, html_body, text_body):
        email_calls.append({
            "to": to,
            "subject": subject,
            "html": html_body,
            "text": text_body,
        })
        return True

    monkeypatch.setattr(admin_routes, "send_push_to_participant", fake_push)
    monkeypatch.setattr(admin_routes, "send_email", fake_email)

    sent = admin_client.post(
        "/admin/communications/deadline-reminders/send",
        data={"deadline_keys": ["2035-03-01T12:00:00"], "force_email": "1"},
        follow_redirects=True,
    )

    assert sent.status_code == 200
    assert "1/1 push envoyé(s)" in sent.text
    assert "2/2 email(s) envoyé(s)" in sent.text
    assert [call["participant_id"] for call in push_calls] == [reachable["id"]]
    assert {call["to"] for call in email_calls} == {reachable["email"], no_push["email"]}
    assert all(call["subject"] == "RESA Pronostics - 1 info à encoder" for call in email_calls)
    assert all("#970301 · Italie – Chili" in call["text"] for call in email_calls)
    assert any(f"/p/{reachable['token']}" in call["text"] for call in email_calls)
    assert any(f"/p/{no_push['token']}" in call["text"] for call in email_calls)


def test_grouped_deadline_reminder_force_email_works_without_push_config(
    admin_client, monkeypatch
):
    clear_operational_data()
    participant = create_participant(name="Mail Only Deadline")
    mark_pre_tournament_submitted(participant["id"])
    create_match(970302, kickoff="2035-03-02T12:00:00", team1="Suisse", team2="Ghana")
    email_calls = []

    async def fake_email(to, subject, html_body, text_body):
        email_calls.append({"to": to, "subject": subject, "text": text_body})
        return True

    monkeypatch.setattr(admin_routes, "push_enabled", lambda: False)
    monkeypatch.setattr(admin_routes, "send_email", fake_email)

    sent = admin_client.post(
        "/admin/communications/deadline-reminders/send",
        data={"deadline_keys": ["2035-03-02T12:00:00"], "force_email": "1"},
        follow_redirects=True,
    )

    assert sent.status_code == 200
    assert "Notifications push non configurées" not in sent.text
    assert "1/1 email(s) envoyé(s)" in sent.text
    assert email_calls == [{
        "to": participant["email"],
        "subject": "RESA Pronostics - 1 info à encoder",
        "text": (
            "Bonjour Mail Only Deadline,\n\n"
            "Il te reste 1 info à encoder sur RESA Pronostics.\n\n"
            "Deadline 02/03/2035 13:00:\n"
            "- #970302 · Suisse – Ghana\n\n"
            f"Compléter mes encodages : {settings.BASE_URL.rstrip('/')}/p/{participant['token']}\n\n"
            "Merci !"
        ),
    }]


def test_push_test_reports_partial_delivery_without_notification_log(
    admin_client, monkeypatch
):
    first = create_participant(name="First Push")
    second = create_participant(name="Second Push")
    before_logs = notification_log_count()
    calls = []

    async def fake_send(db, participant_id, *, title, body, url):
        calls.append(participant_id)
        return participant_id == first["id"]

    monkeypatch.setattr(admin_routes, "push_enabled", lambda: True)
    monkeypatch.setattr(admin_routes, "send_push_to_participant", fake_send)

    response = admin_client.post(
        "/admin/communications/send-push-test",
        data={
            "participant_ids": [str(first["id"]), str(second["id"])],
            "title": "Test",
            "body": "Message",
            "destination": "profil",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "1/2 notification(s) push envoyée(s)" in response.text
    assert "1 sans abonnement actif ou en échec" in response.text
    assert calls == [first["id"], second["id"]]
    assert notification_log_count() == before_logs
