"""Story des nouveautés : fetch, marquage vu, CRUD admin."""
from app.database import get_db
from tests.conftest import run


def _insert_news(slug, title, published=1, sort_order=0):
    async def _ins():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO news_items (slug, title, body, is_published, sort_order)
                   VALUES (?,?,?,?,?)""",
                (slug, title, "Détail", published, sort_order),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_ins())


def _last_seen(participant_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT last_seen_news_id FROM participants WHERE id=?", (participant_id,)
            )
            return (await row.fetchone())["last_seen_news_id"]

    return run(_get())


def test_story_shows_unseen_published_only(client, participant):
    pub = _insert_news("pub-1", "Annonce Test Unique", published=1)
    _insert_news("draft-1", "Brouillon caché", published=0)

    html = client.get(f"/p/{participant['token']}").text
    assert "Annonce Test Unique" in html
    assert "data-story" in html
    assert "Brouillon caché" not in html

    # Une fois marquée vue, la story ne réapparaît plus.
    res = client.post(
        f"/api/news/seen?token={participant['token']}", json={"id": pub}
    )
    assert res.status_code == 200
    html2 = client.get(f"/p/{participant['token']}").text
    assert "Annonce Test Unique" not in html2


def test_news_seen_is_monotonic(client, participant):
    client.post(f"/api/news/seen?token={participant['token']}", json={"id": 5})
    assert _last_seen(participant["id"]) == 5
    # Un id plus petit ne fait jamais reculer le repère.
    client.post(f"/api/news/seen?token={participant['token']}", json={"id": 2})
    assert _last_seen(participant["id"]) == 5


def test_news_seen_rejects_bad_token(client):
    res = client.post("/api/news/seen?token=nope", json={"id": 1})
    assert res.status_code == 403


def test_cabinet_story_shows_home_then_profile_steps(client, participant):
    html = client.get(f"/p/{participant['token']}").text
    assert "Le Cabinet à trophées" in html
    assert "Sur l'accueil" in html
    assert "Sur ton profil" in html


def test_admin_news_crud(admin_client):
    # Création
    res = admin_client.post(
        "/admin/nouveautes",
        data={"title": "Nouvelle feature", "body": "x", "icon": "🚀",
              "sort_order": "1", "is_published": "1"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    listing = admin_client.get("/admin/nouveautes").text
    assert "Nouvelle feature" in listing

    async def _find():
        async with get_db() as db:
            row = await db.execute(
                "SELECT id, is_published FROM news_items WHERE title=?", ("Nouvelle feature",)
            )
            return dict(await row.fetchone())

    item = run(_find())
    assert item["is_published"] == 1

    # Suppression
    res = admin_client.post(
        f"/admin/nouveautes/{item['id']}/delete", follow_redirects=False
    )
    assert res.status_code == 303
    listing2 = admin_client.get("/admin/nouveautes").text
    assert "Nouvelle feature" not in listing2
