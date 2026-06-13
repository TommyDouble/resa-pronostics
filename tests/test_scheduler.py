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


def make_push_participant():
    """Participant joignable par push (abonnement actif) — éligible aux rappels push only."""
    p = make_participant(opt_in=0)

    async def _subscribe():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO push_subscriptions (participant_id, endpoint, p256dh, auth)
                   VALUES (?,?, 'k', 'a')""",
                (p["id"], f"https://push.test/{p['token']}"),
            )
            await db.commit()

    run(_subscribe())
    return p


def make_match_in_minutes(number, minutes):
    """Crée un match dont le coup d'envoi (UTC) tombe à now+minutes."""
    target = now_utc() + timedelta(minutes=minutes)

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, group_name, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'group', 'Groupe T', ?, ?, 'France', 'Brésil', 1)""",
                (number, target.strftime("%Y-%m-%d"), target.strftime("%H:%M:%S")),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def make_match_yesterday_with_result(number):
    """Match daté de la veille (locale) avec résultat encodé."""
    target = now_utc().astimezone(DISPLAY_TZ).replace(hour=12, minute=0) - timedelta(days=1)

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, group_name, match_date, kickoff_time,
                                        team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?, 'group', 'Groupe T', ?, ?, 'France', 'Brésil', 1, 2, 0, 'team1')""",
                (number, target.strftime("%Y-%m-%d"), target.strftime("%H:%M:%S")),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def get_snapshots(participant_id, snapshot_date):
    async def _get():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT * FROM ranking_snapshots WHERE participant_id=? AND snapshot_date=?",
                (participant_id, snapshot_date),
            )
            return [dict(r) for r in await rows.fetchall()]

    return run(_get())


def morning_9h():
    return now_utc().astimezone(DISPLAY_TZ).replace(hour=9, minute=0)


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


def test_morning_reminder_push_only_and_idempotent(client):
    p_push = make_push_participant()
    p_no_push = make_participant(opt_in=1)
    make_match_in_minutes(910001, 300)  # dans la fenêtre du matin, hors escalade

    run(run_pending_notifications(now_local=morning_9h()))
    # Push éligible → un rappel matinal ; pas d'abonnement → rien (push only).
    assert len(get_log(p_push["id"], "enc_morning")) == 1
    assert get_log(p_no_push["id"], "enc_morning") == []

    # Deuxième passe: pas de doublon (ref = date du jour).
    run(run_pending_notifications(now_local=morning_9h()))
    assert len(get_log(p_push["id"], "enc_morning")) == 1


def test_morning_reminder_skipped_when_all_predicted(client):
    p = make_push_participant()

    async def _predict_all():
        async with get_db() as db:
            rows = await db.execute("SELECT id FROM matches")
            for r in await rows.fetchall():
                await db.execute(
                    """INSERT INTO predictions (participant_id, match_id, prediction,
                                                exact_score_team1, exact_score_team2)
                       VALUES (?,?, 'team1', 2, 0)
                       ON CONFLICT(participant_id, match_id) DO NOTHING""",
                    (p["id"], r["id"]),
                )
            await db.commit()

    run(_predict_all())
    run(run_pending_notifications(now_local=morning_9h()))
    assert get_log(p["id"], "enc_morning") == []


def test_morning_reminder_not_before_9h(client):
    p = make_push_participant()
    make_match_in_minutes(910004, 300)
    before = now_utc().astimezone(DISPLAY_TZ).replace(hour=8, minute=0)
    run(run_pending_notifications(now_local=before))
    assert get_log(p["id"], "enc_morning") == []


def test_lock_escalation_2h_then_1h(client):
    p = make_push_participant()
    m_2h = make_match_in_minutes(910010, 90)   # dans la fenêtre 2 h
    m_1h = make_match_in_minutes(910011, 40)   # urgence : 1 h

    # À 18 h le rappel matinal ne se déclenche pas, seule l'escalade joue.
    run(run_pending_notifications(now_local=evening()))
    enc_2h = get_log(p["id"], "enc_2h")
    enc_1h = get_log(p["id"], "enc_1h")
    assert [r["ref"] for r in enc_2h] == [str(m_2h)]
    assert [r["ref"] for r in enc_1h] == [str(m_1h)]

    # Idempotent: pas de doublon par (match, palier).
    run(run_pending_notifications(now_local=evening()))
    assert len(get_log(p["id"], "enc_2h")) == 1
    assert len(get_log(p["id"], "enc_1h")) == 1


def test_snapshot_capture_idempotent(client):
    p = make_participant()
    make_match_yesterday_with_result(910020)
    yesterday = local_today(-1).isoformat()
    afternoon = now_utc().astimezone(DISPLAY_TZ).replace(hour=14, minute=0)

    run(run_pending_notifications(now_local=afternoon))
    assert len(get_snapshots(p["id"], yesterday)) == 1

    run(run_pending_notifications(now_local=afternoon))
    assert len(get_snapshots(p["id"], yesterday)) == 1


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
