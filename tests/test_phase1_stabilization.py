import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.database as database
from app.auth import hash_password
from app.config import settings
from app.mail import send_email
from app.main import app
from app.scoring import (
    get_rankings,
    recalculate_match_scores,
    recalculate_pre_tournament_scores,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(tmp_path: Path):
    database.DB_PATH = str(tmp_path / "resa-test.db")
    with TestClient(app) as test_client:
        yield test_client


async def exec_sql(sql, params=()):
    async with database.get_db() as db:
        await db.execute(sql, params)
        await db.commit()


async def fetch_one(sql, params=()):
    async with database.get_db() as db:
        row = await (await db.execute(sql, params)).fetchone()
        return dict(row) if row else None


async def seed_participant(token="token-a", name="Alice Test", email="alice@example.com", active=1):
    await exec_sql(
        """INSERT INTO participants (name, first_name, last_name, email, token, is_confirmed, is_active)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (name, name.split()[0], " ".join(name.split()[1:]), email, token, active),
    )


async def seed_admin():
    await exec_sql(
        "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
        ("admin", hash_password("admin123")),
    )


def login_admin(client):
    return client.post(
        "/admin/login",
        data={"username": "admin", "password": "admin123"},
        follow_redirects=False,
    )


def test_match_prediction_is_locked_and_score_must_match_outcome(client):
    run(seed_participant())
    run(exec_sql(
        """INSERT INTO matches (match_number, phase, match_date, kickoff_time, team1_name, team2_name)
           VALUES (1, 'group', '2099-01-01', '12:00:00', 'France', 'Belgique')"""
    ))
    run(exec_sql(
        """INSERT INTO matches (match_number, phase, match_date, kickoff_time, team1_name, team2_name)
           VALUES (2, 'group', '2000-01-01', '12:00:00', 'Espagne', 'Portugal')"""
    ))

    ok = client.post(
        "/api/predictions?token=token-a",
        json={"match_id": 1, "prediction": "team1", "exact_score_team1": 2, "exact_score_team2": 1},
    )
    assert ok.status_code == 200

    bad_score = client.post(
        "/api/predictions?token=token-a",
        json={"match_id": 1, "prediction": "team1", "exact_score_team1": 0, "exact_score_team2": 1},
    )
    assert bad_score.status_code == 400

    locked = client.post(
        "/api/predictions?token=token-a",
        json={"match_id": 2, "prediction": "draw"},
    )
    assert locked.status_code == 403


def test_bonus_answer_is_locked_after_deadline_or_scoring(client):
    run(seed_participant())
    run(exec_sql(
        """INSERT INTO bonus_questions (question_text, phase, answer_type, points_value, deadline)
           VALUES ('Question ouverte', 'quarter', 'text', 5, '2099-01-01T12:00:00')"""
    ))
    run(exec_sql(
        """INSERT INTO bonus_questions (question_text, phase, answer_type, points_value, deadline)
           VALUES ('Question fermee', 'quarter', 'text', 5, '2000-01-01T12:00:00')"""
    ))

    first = client.post(
        "/p/token-a/bonus/1",
        data={"answer": "France"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    run(exec_sql("UPDATE bonus_questions SET correct_answer='France' WHERE id=1"))
    scored = client.post(
        "/p/token-a/bonus/1",
        data={"answer": "Belgique"},
        follow_redirects=False,
    )
    assert scored.status_code == 403

    expired = client.post(
        "/p/token-a/bonus/2",
        data={"answer": "France"},
        follow_redirects=False,
    )
    assert expired.status_code == 403


def test_pre_tournament_deadline_blocks_submission(client):
    run(seed_participant())
    run(exec_sql(
        """INSERT INTO app_settings (key, value) VALUES ('pre_tournament_deadline', '2000-01-01T12:00:00')
           ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
    ))

    response = client.post(
        "/p/token-a/pre-tournoi",
        data={
            "winner": "France",
            "finalist": "Belgique",
            "top_scorer": "Kylian Mbappé",
            "revelation": "Maroc",
            "total_goals": "100",
            "action": "submit",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    saved = run(fetch_one("SELECT * FROM pre_tournament_predictions WHERE participant_id=1"))
    assert saved is None


def test_inactive_participant_token_and_ranking_are_blocked(client):
    run(seed_admin())
    run(seed_participant())
    assert login_admin(client).status_code == 303

    before = run(get_rankings())
    assert [r["name"] for r in before] == ["Alice Test"]

    response = client.post("/admin/participants/1/delete", follow_redirects=False)
    assert response.status_code == 303

    assert client.get("/p/token-a").status_code == 404
    after = run(get_rankings())
    assert after == []


def test_pre_tournament_scores_are_included_and_ranking_tiebreakers_apply(client):
    run(seed_participant(token="token-a", name="Alice Test", email="alice@example.com"))
    run(seed_participant(token="token-b", name="Bob Test", email="bob@example.com"))
    run(exec_sql(
        """INSERT INTO pre_tournament_predictions
           (participant_id, total_goals, submitted, submitted_at)
           VALUES (1, 102, 1, '2026-06-01T10:00:00')"""
    ))
    run(exec_sql(
        """INSERT INTO pre_tournament_official_answers (key, answer)
           VALUES ('total_goals', '100')"""
    ))
    run(exec_sql(
        """INSERT INTO matches
           (match_number, phase, match_date, kickoff_time, team1_name, team2_name, score_team1, score_team2, result)
           VALUES (1, 'group', '2000-01-01', '12:00:00', 'France', 'Belgique', 1, 0, 'team1')"""
    ))
    run(exec_sql(
        """INSERT INTO predictions
           (participant_id, match_id, prediction, exact_score_team1, exact_score_team2)
           VALUES (2, 1, 'team1', 1, 0)"""
    ))

    run(recalculate_pre_tournament_scores())
    run(recalculate_match_scores(1))
    rankings = run(get_rankings())

    assert rankings[0]["name"] == "Bob Test"
    assert rankings[0]["total_points"] == 4
    assert rankings[0]["exact_count"] == 1
    assert rankings[1]["name"] == "Alice Test"
    assert rankings[1]["total_points"] == 4
    assert rankings[1]["pre_tournament_points"] == 4


def test_send_email_returns_false_without_smtp(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    sent = run(send_email("test@example.com", "Test", "<p>Test</p>", "Test"))
    assert sent is False
