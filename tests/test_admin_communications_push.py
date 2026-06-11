"""Admin communications: manual targeted push tests."""
import uuid

import app.routers.admin as admin_routes
from app.config import settings
from app.database import get_db
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


def test_communications_shows_manual_push_card(admin_client, participant, monkeypatch):
    add_subscription(participant["id"])
    monkeypatch.setattr(admin_routes, "push_enabled", lambda: True)

    response = admin_client.get("/admin/communications")

    assert response.status_code == 200
    assert "Notification push de test" in response.text
    assert f"{participant['token']}@test.local" in response.text
    assert "1 appareil" in response.text
    assert "Envoyer le test push" in response.text


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
