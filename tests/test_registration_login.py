"""Integration tests: registration with department + email/password login."""
import uuid

from app.constants import DEPARTMENTS
from app.database import get_db
from tests.conftest import run


def register_payload(email, **overrides):
    payload = {
        "first_name": "Marie",
        "last_name": "Curie",
        "email": email,
        "department": DEPARTMENTS[0],
        "password": "motdepasse8",
        "password_confirm": "motdepasse8",
    }
    payload.update(overrides)
    return payload


def get_participant(email):
    async def _get():
        async with get_db() as db:
            row = await db.execute("SELECT * FROM participants WHERE email=?", (email,))
            result = await row.fetchone()
            return dict(result) if result else None

    return run(_get())


def unique_email():
    return f"{uuid.uuid4().hex[:10]}@test.local"


class TestRegistration:
    def test_register_creates_confirmed_participant(self, client):
        email = unique_email()
        response = client.post("/rejoindre", data=register_payload(email), follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/p/")
        p = get_participant(email)
        assert p["is_confirmed"] == 1
        assert p["department"] == DEPARTMENTS[0]
        assert p["first_name"] == "Marie"
        assert p["password_hash"] and p["password_hash"] != "motdepasse8"

    def test_register_requires_department(self, client):
        email = unique_email()
        response = client.post("/rejoindre", data=register_payload(email, department=""))
        assert response.status_code == 200
        assert "département" in response.text
        assert get_participant(email) is None

    def test_register_rejects_unknown_department(self, client):
        email = unique_email()
        response = client.post("/rejoindre", data=register_payload(email, department="Comptabilité"))
        assert get_participant(email) is None

    def test_register_password_mismatch(self, client):
        email = unique_email()
        response = client.post(
            "/rejoindre", data=register_payload(email, password_confirm="autremotdepasse")
        )
        assert "correspondent pas" in response.text
        assert get_participant(email) is None

    def test_register_short_password(self, client):
        email = unique_email()
        response = client.post(
            "/rejoindre", data=register_payload(email, password="abc", password_confirm="abc")
        )
        assert get_participant(email) is None

    def test_register_duplicate_email_does_not_leak_token(self, client):
        email = unique_email()
        client.post("/rejoindre", data=register_payload(email), follow_redirects=False)
        token = get_participant(email)["token"]
        response = client.post("/rejoindre", data=register_payload(email), follow_redirects=False)
        # No redirect to the existing participant space, no token in the page
        assert response.status_code == 200
        assert "déjà inscrit" in response.text
        assert token not in response.text


class TestLogin:
    def test_login_page_at_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Se connecter" in response.text

    def test_login_success_redirects_to_token_space(self, client):
        email = unique_email()
        client.post("/rejoindre", data=register_payload(email), follow_redirects=False)
        token = get_participant(email)["token"]
        response = client.post(
            "/connexion", data={"email": email, "password": "motdepasse8"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/p/{token}"

    def test_login_wrong_password(self, client):
        email = unique_email()
        client.post("/rejoindre", data=register_payload(email), follow_redirects=False)
        response = client.post(
            "/connexion", data={"email": email, "password": "mauvaismdp1"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "incorrect" in response.text

    def test_login_unknown_email(self, client):
        response = client.post(
            "/connexion", data={"email": unique_email(), "password": "motdepasse8"},
        )
        assert "incorrect" in response.text

    def test_login_account_without_password(self, client, participant):
        # `participant` fixture creates an admin-invited account (no password)
        async def _email():
            async with get_db() as db:
                row = await db.execute(
                    "SELECT email FROM participants WHERE id=?", (participant["id"],)
                )
                return (await row.fetchone())["email"]

        email = run(_email())
        response = client.post(
            "/connexion", data={"email": email, "password": "nimportequoi"},
        )
        assert "lien personnel" in response.text


class TestProfilePassword:
    def test_set_password_from_profile_then_login(self, client, participant):
        token = participant["token"]
        response = client.post(
            f"/p/{token}/profil/edit",
            data={
                "first_name": "Test", "last_name": "User",
                "department": DEPARTMENTS[2],
                "new_password": "nouveaumdp8", "new_password_confirm": "nouveaumdp8",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "msg=password_set" in response.headers["location"]

        async def _email():
            async with get_db() as db:
                row = await db.execute(
                    "SELECT email, department FROM participants WHERE id=?",
                    (participant["id"],),
                )
                return dict(await row.fetchone())

        data = run(_email())
        assert data["department"] == DEPARTMENTS[2]
        login = client.post(
            "/connexion", data={"email": data["email"], "password": "nouveaumdp8"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == f"/p/{token}"

    def test_password_mismatch_keeps_old_login(self, client, participant):
        token = participant["token"]
        response = client.post(
            f"/p/{token}/profil/edit",
            data={
                "first_name": "Test", "last_name": "User",
                "new_password": "unmotdepasse", "new_password_confirm": "unautre",
            },
            follow_redirects=False,
        )
        assert "msg=password_mismatch" in response.headers["location"]
