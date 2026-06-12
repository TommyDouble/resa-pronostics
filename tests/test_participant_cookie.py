"""Cookie de reconnexion participant : la PWA (start_url '/') retrouve le compte."""
import uuid

from app.auth import hash_password
from app.database import get_db
from tests.conftest import run

COOKIE = "resa_token"


def _make_participant(password=None):
    token = str(uuid.uuid4())
    email = f"{token}@test.local"

    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed, password_hash)
                   VALUES (?,?,?,1,?)""",
                ("Cookie User", email, token, hash_password(password) if password else None),
            )
            await db.commit()

    run(_create())
    return token, email


def test_visiting_participant_page_sets_cookie(client):
    client.cookies.delete(COOKIE) if COOKIE in client.cookies else None
    token, _ = _make_participant()
    resp = client.get(f"/p/{token}")
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert f"{COOKIE}={token}" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Max-Age=15552000" in set_cookie  # 180 jours
    client.cookies.delete(COOKIE)


def test_root_redirects_with_valid_cookie(client):
    token, _ = _make_participant()
    client.cookies.set(COOKIE, token)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/p/{token}"
    client.cookies.delete(COOKIE)


def test_root_shows_login_and_purges_stale_cookie(client):
    client.cookies.set(COOKIE, "token-fantome")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert 'name="password"' in resp.text  # formulaire de login
    set_cookie = resp.headers.get("set-cookie", "")
    assert f'{COOKIE}="";' in set_cookie or f"{COOKIE}=;" in set_cookie
    client.cookies.delete(COOKIE) if COOKIE in client.cookies else None


def test_login_sets_cookie(client):
    token, email = _make_participant(password="motdepasse123")
    resp = client.post(
        "/connexion",
        data={"email": email, "password": "motdepasse123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/p/{token}"
    assert f"{COOKIE}={token}" in resp.headers.get("set-cookie", "")
    client.cookies.delete(COOKIE)


def test_invalid_token_page_does_not_set_cookie(client):
    resp = client.get("/p/token-inexistant")
    assert resp.status_code == 404
    assert COOKIE not in resp.headers.get("set-cookie", "")


def test_logout_clears_cookie(client):
    token, _ = _make_participant()
    client.cookies.set(COOKIE, token)
    resp = client.post("/deconnexion", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    set_cookie = resp.headers.get("set-cookie", "")
    assert f'{COOKIE}="";' in set_cookie or f"{COOKIE}=;" in set_cookie
    client.cookies.delete(COOKIE) if COOKIE in client.cookies else None
