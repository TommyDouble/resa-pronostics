"""Notifications push: endpoints d'abonnement et bascule email."""
from app.database import get_db
from app.push import send_push_to_participant
from tests.conftest import run


SUBSCRIPTION = {
    "endpoint": "https://push.example.com/sub/abc123",
    "keys": {"p256dh": "fake-p256dh", "auth": "fake-auth"},
}


def get_subs(participant_id):
    async def _get():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT * FROM push_subscriptions WHERE participant_id=?",
                (participant_id,),
            )
            return [dict(r) for r in await rows.fetchall()]

    return run(_get())


def test_push_config_disabled_without_vapid_keys(client, participant):
    response = client.get(f"/api/push/config?token={participant['token']}")
    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_subscribe_then_unsubscribe(client, participant):
    response = client.post(
        f"/api/push/subscribe?token={participant['token']}", json=SUBSCRIPTION
    )
    assert response.status_code == 200
    assert len(get_subs(participant["id"])) == 1

    # Ré-abonnement avec le même endpoint: upsert, pas de doublon.
    client.post(f"/api/push/subscribe?token={participant['token']}", json=SUBSCRIPTION)
    assert len(get_subs(participant["id"])) == 1

    response = client.post(
        f"/api/push/unsubscribe?token={participant['token']}",
        json={"endpoint": SUBSCRIPTION["endpoint"]},
    )
    assert response.status_code == 200
    assert get_subs(participant["id"]) == []


def test_subscribe_rejects_incomplete_payload(client, participant):
    response = client.post(
        f"/api/push/subscribe?token={participant['token']}",
        json={"endpoint": "https://push.example.com/x", "keys": {}},
    )
    assert response.status_code == 400


def test_subscribe_requires_valid_token(client):
    response = client.post("/api/push/subscribe?token=invalid", json=SUBSCRIPTION)
    assert response.status_code == 403


def test_send_push_returns_false_without_keys(client, participant):
    async def _send():
        async with get_db() as db:
            return await send_push_to_participant(
                db, participant["id"], title="t", body="b", url="http://x"
            )

    assert run(_send()) is False
