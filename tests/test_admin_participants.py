"""Admin participant management: deletion must actually remove the participant."""
import io
import uuid

import app.routers.admin as admin_routes
from app.database import get_db
from tests.conftest import run


def _participant_with_data():
    """Create a confirmed participant plus a submitted pre-tournament prediction
    and a pre-tournament score, returning their id."""
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, first_name, last_name, email, token, is_confirmed)
                   VALUES (?,?,?,?,?,1)""",
                ("Test Delete", "Test", "Delete", f"{token}@test.local", token),
            )
            pid = cursor.lastrowid
            await db.execute(
                """INSERT INTO pre_tournament_predictions (participant_id, winner, submitted)
                   VALUES (?, 'France', 1)""",
                (pid,),
            )
            await db.execute(
                """INSERT INTO pre_tournament_scores (participant_id, question_key, points)
                   VALUES (?, 'winner', 8)""",
                (pid,),
            )
            await db.commit()
            return pid

    return run(_create())


def _counts(pid):
    async def _q():
        async with get_db() as db:
            p = await (await db.execute("SELECT COUNT(*) c FROM participants WHERE id=?", (pid,))).fetchone()
            pt = await (await db.execute("SELECT COUNT(*) c FROM pre_tournament_predictions WHERE participant_id=?", (pid,))).fetchone()
            ps = await (await db.execute("SELECT COUNT(*) c FROM pre_tournament_scores WHERE participant_id=?", (pid,))).fetchone()
            return p["c"], pt["c"], ps["c"]

    return run(_q())


def _participant_by_email(email):
    async def _q():
        async with get_db() as db:
            row = await db.execute("SELECT * FROM participants WHERE email=?", (email,))
            result = await row.fetchone()
            return dict(result) if result else None

    return run(_q())


def test_delete_participant_removes_them_and_cascades(admin_client):
    pid = _participant_with_data()
    assert _counts(pid) == (1, 1, 1)

    response = admin_client.post(
        f"/admin/participants/{pid}/delete", follow_redirects=False
    )
    assert response.status_code == 303
    # Participant and all their related rows are gone (foreign keys cascade).
    assert _counts(pid) == (0, 0, 0)


def test_delete_missing_participant_is_safe(admin_client):
    response = admin_client.post(
        "/admin/participants/999999/delete", follow_redirects=False
    )
    assert response.status_code == 303


def test_add_participant_normalizes_name_casing(admin_client, monkeypatch):
    async def fake_invitation(participant):
        return True

    monkeypatch.setattr(admin_routes, "send_invitation", fake_invitation)
    email = f"{uuid.uuid4().hex}@test.local"

    response = admin_client.post(
        "/admin/participants/add",
        data={"name": "jean-pierre DUPONT", "email": email},
        follow_redirects=False,
    )

    assert response.status_code == 303
    participant = _participant_by_email(email)
    assert participant["name"] == "Jean-Pierre Dupont"
    assert participant["first_name"] == "Jean-Pierre"
    assert participant["last_name"] == "Dupont"


def test_import_csv_normalizes_name_casing(admin_client, monkeypatch):
    async def fake_invitation(participant):
        return True

    monkeypatch.setattr(admin_routes, "send_invitation", fake_invitation)
    email = f"{uuid.uuid4().hex}@test.local"
    csv_content = f"nom,email\nanne-sophie d'ANGELO,{email}\n".encode()

    response = admin_client.post(
        "/admin/participants/import",
        files={"csv_file": ("participants.csv", io.BytesIO(csv_content), "text/csv")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    participant = _participant_by_email(email)
    assert participant["name"] == "Anne-Sophie D'Angelo"
    assert participant["first_name"] == "Anne-Sophie"
    assert participant["last_name"] == "D'Angelo"
