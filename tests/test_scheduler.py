"""Rappels automatiques: passes idempotentes du scheduler."""
import uuid
from datetime import datetime, timedelta

from app.database import get_db
from app.scheduler import run_pending_notifications
from app.timeutils import DISPLAY_TZ, local_today, now_utc
from tests.conftest import run


def make_participant(opt_in=1):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed, email_opt_in)
                   VALUES (?,?,?,1,?)""",
                ("Sched User", f"{token}@test.local", token, opt_in),
            )
            await db.commit()
            return cursor.lastrowid

    return {"id": run(_create()), "token": token}


def make_match_tomorrow(number):
    tomorrow = local_today(1).isoformat()

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, group_name, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'group', 'Groupe T', ?, '12:00', 'France', 'Brésil', 1)""",
                (number, tomorrow),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def get_log(participant_id, kind):
    async def _get():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT * FROM notification_log WHERE participant_id=? AND kind=?",
                (participant_id, kind),
            )
            return [dict(r) for r in await rows.fetchall()]

    return run(_get())


def evening():
    return now_utc().astimezone(DISPLAY_TZ).replace(hour=18, minute=0)


def test_match_reminder_sent_once_and_only_to_opted_in(client):
    p_in = make_participant(opt_in=1)
    p_out = make_participant(opt_in=0)
    make_match_tomorrow(910001)

    run(run_pending_notifications(now_local=evening()))
    assert len(get_log(p_in["id"], "match_reminder")) == 1
    assert get_log(p_out["id"], "match_reminder") == []

    # Deuxième passe: pas de doublon.
    run(run_pending_notifications(now_local=evening()))
    assert len(get_log(p_in["id"], "match_reminder")) == 1


def test_match_reminder_skipped_when_already_predicted(client):
    p = make_participant()
    match_id = make_match_tomorrow(910002)

    async def _predict():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                                            exact_score_team1, exact_score_team2)
                   VALUES (?,?, 'team1', 2, 0)""",
                (p["id"], match_id),
            )
            # Couvre aussi les autres matchs de demain créés par d'autres tests.
            rows = await db.execute(
                "SELECT id FROM matches WHERE match_date=?", (local_today(1).isoformat(),)
            )
            for r in await rows.fetchall():
                await db.execute(
                    """INSERT INTO predictions (participant_id, match_id, prediction,
                                                exact_score_team1, exact_score_team2)
                       VALUES (?,?, 'team1', 2, 0)
                       ON CONFLICT(participant_id, match_id) DO NOTHING""",
                    (p["id"], r["id"]),
                )
            await db.commit()

    run(_predict())
    run(run_pending_notifications(now_local=evening()))
    assert get_log(p["id"], "match_reminder") == []


def test_no_match_reminder_before_late_afternoon(client):
    p = make_participant()
    make_match_tomorrow(910003)
    morning = now_utc().astimezone(DISPLAY_TZ).replace(hour=8, minute=0)
    run(run_pending_notifications(now_local=morning))
    assert get_log(p["id"], "match_reminder") == []


def test_pre_tournament_reminder_when_deadline_close(client):
    p = make_participant()
    deadline = (now_utc() + timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S")

    async def _set_deadline():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO app_settings (key, value) VALUES ('pre_tournament_deadline', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (deadline,),
            )
            await db.commit()

    run(_set_deadline())
    run(run_pending_notifications(now_local=evening()))
    assert len(get_log(p["id"], "pt_reminder")) == 1
    run(run_pending_notifications(now_local=evening()))
    assert len(get_log(p["id"], "pt_reminder")) == 1


def test_bonus_reminder_only_for_unanswered(client):
    p_answered = make_participant()
    p_silent = make_participant()
    deadline = (now_utc() + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")

    async def _seed():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions (question_text, phase, answer_type, options, points_value, deadline)
                   VALUES ('Test bonus ?', 'quarter', 'choice', '["Oui","Non"]', 5, ?)""",
                (deadline,),
            )
            qid = cursor.lastrowid
            await db.execute(
                "INSERT INTO bonus_answers (participant_id, question_id, answer) VALUES (?,?, 'Oui')",
                (p_answered["id"], qid),
            )
            await db.commit()

    run(_seed())
    run(run_pending_notifications(now_local=evening()))
    assert get_log(p_answered["id"], "bonus_reminder") == []
    assert len(get_log(p_silent["id"], "bonus_reminder")) == 1
