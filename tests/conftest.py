import os
import tempfile

# Must be set before any app module imports settings.
_db_file = tempfile.NamedTemporaryFile(prefix="resa_test_", suffix=".db", delete=False)
os.environ["DATABASE_URL"] = _db_file.name
os.environ["SCHEDULER_ENABLED"] = "0"
_db_file.close()

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.database import get_db, init_db
from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:  # lifespan runs init_db
        yield test_client
    os.unlink(os.environ["DATABASE_URL"])


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def participant(client):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, first_name, last_name, email, token, is_confirmed)
                   VALUES (?,?,?,?,?,1)""",
                ("Test User", "Test", "User", f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid

    participant_id = run(_create())
    return {"id": participant_id, "token": token}


@pytest.fixture()
def admin_client(client):
    async def _create_admin():
        async with get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO admin_users (username, password_hash) VALUES (?, ?)",
                ("testadmin", hash_password("testpass")),
            )
            await db.commit()

    run(_create_admin())
    response = client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "testpass"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client
