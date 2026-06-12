"""Admin: le toggle paiement répond en JSON (mise à jour sans rechargement)."""
import uuid

from app.database import get_db
from tests.conftest import run


def _make_participant():
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                ("Paie Toggle", f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _has_paid(pid):
    async def _q():
        async with get_db() as db:
            row = await db.execute("SELECT has_paid FROM participants WHERE id=?", (pid,))
            return (await row.fetchone())["has_paid"]

    return run(_q())


def test_toggle_paid_returns_json_and_flips(admin_client):
    pid = _make_participant()
    r = admin_client.post(f"/admin/participants/{pid}/toggle-paid")
    assert r.status_code == 200
    assert r.json() == {"has_paid": 1}
    assert _has_paid(pid) == 1

    r = admin_client.post(f"/admin/participants/{pid}/toggle-paid")
    assert r.json() == {"has_paid": 0}
    assert _has_paid(pid) == 0


def test_toggle_paid_unknown_participant(admin_client):
    assert admin_client.post("/admin/participants/999999/toggle-paid").status_code == 404
