"""Easter egg temporaire "Folarin Balogun" (écran classement uniquement)."""
import uuid
from datetime import datetime, timezone

from app.database import get_db
from app.easter_egg import (
    BALOGUN_NAME,
    apply_balogun_swap,
    get_next_match,
    is_balogun_easter_egg_active,
)
from app.routers import pages as pages_router
from tests.conftest import run


def make_participant(name):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed)
                   VALUES (?,?,?,1)""",
                (name, f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid

    return {"id": run(_create()), "token": token}


def make_match(match_date, kickoff_time, number, team1="Belgique", team2="Maroc"):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'group', ?, ?, ?, ?, 1)""",
                (number, match_date, kickoff_time, team1, team2),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


# ---- is_balogun_easter_egg_active (logique pure) ----------------------

def test_active_before_kickoff():
    next_match = {"match_date": "2030-06-15", "kickoff_time": "20:00"}
    now = datetime(2030, 6, 15, 19, 59, tzinfo=timezone.utc)
    assert is_balogun_easter_egg_active(now, next_match) is True


def test_inactive_at_and_after_kickoff():
    next_match = {"match_date": "2030-06-15", "kickoff_time": "20:00"}
    at_kickoff = datetime(2030, 6, 15, 20, 0, tzinfo=timezone.utc)
    after_kickoff = datetime(2030, 6, 15, 20, 1, tzinfo=timezone.utc)
    assert is_balogun_easter_egg_active(at_kickoff, next_match) is False
    assert is_balogun_easter_egg_active(after_kickoff, next_match) is False


def test_inactive_when_no_next_match_found():
    now = datetime(2030, 6, 15, 12, 0, tzinfo=timezone.utc)
    assert is_balogun_easter_egg_active(now, None) is False


# ---- get_next_match (accès BDD) ----------------------------------------

def test_get_next_match_returns_earliest_upcoming(client, monkeypatch):
    monkeypatch.setenv("FAKE_NOW_UTC", "2999-01-01T00:00:00")
    make_match("3000-06-10", "18:00", 900001, "Belgique", "Maroc")
    later_id = make_match("3000-06-20", "18:00", 900002, "France", "Brésil")

    async def _fetch():
        async with get_db() as db:
            return await get_next_match(db)

    result = run(_fetch())
    assert result is not None
    assert result["match_date"] == "3000-06-10"
    assert result["id"] != later_id


def test_get_next_match_returns_none_when_none_upcoming(client, monkeypatch):
    monkeypatch.setenv("FAKE_NOW_UTC", "9999-12-31T23:59:59")

    async def _fetch():
        async with get_db() as db:
            return await get_next_match(db)

    assert run(_fetch()) is None


# ---- apply_balogun_swap ne touche jamais la BDD ------------------------

def test_apply_balogun_swap_does_not_touch_db(client):
    alice = make_participant("Alice Balogun-Test")
    ctx = {"rankings": [{"id": alice["id"], "name": "Alice Balogun-Test", "full_name": "Alice Balogun-Test", "badges": []}]}
    apply_balogun_swap(ctx)
    assert ctx["rankings"][0]["name"] == BALOGUN_NAME
    assert ctx["rankings"][0]["real_name"] == "Alice Balogun-Test"

    async def _fetch_real_name():
        async with get_db() as db:
            row = await db.execute("SELECT name FROM participants WHERE id = ?", (alice["id"],))
            return (await row.fetchone())["name"]

    assert run(_fetch_real_name()) == "Alice Balogun-Test"


# ---- Écran classement : rendu HTML -------------------------------------

def test_ranking_page_shows_balogun_when_active(client, monkeypatch):
    alice = make_participant("Alice RankingEgg")
    monkeypatch.setattr(pages_router, "is_balogun_easter_egg_active", lambda now, m: True)

    response = client.get(f"/p/{alice['token']}/classement")
    assert response.status_code == 200
    html = response.text
    assert f">{BALOGUN_NAME}</span>" in html
    assert 'data-real-name="Alice RankingEgg"' in html
    assert 'aria-label="Nom réel : Alice RankingEgg"' in html
    assert ">Alice RankingEgg</span>" not in html


def test_ranking_page_shows_real_name_when_inactive(client, monkeypatch):
    alice = make_participant("Bob RankingEgg")
    monkeypatch.setattr(pages_router, "is_balogun_easter_egg_active", lambda now, m: False)

    response = client.get(f"/p/{alice['token']}/classement")
    assert response.status_code == 200
    html = response.text
    assert BALOGUN_NAME not in html
    assert "Bob RankingEgg" in html
    assert "data-real-name=" not in html


def test_easter_egg_does_not_affect_other_screens(client, monkeypatch):
    alice = make_participant("Carol RankingEgg")
    monkeypatch.setattr(pages_router, "is_balogun_easter_egg_active", lambda now, m: True)

    ranking_html = client.get(f"/p/{alice['token']}/classement").text
    assert BALOGUN_NAME in ranking_html

    profile_html = client.get(f"/p/{alice['token']}/profil").text
    assert BALOGUN_NAME not in profile_html
    assert "Carol RankingEgg" in profile_html
