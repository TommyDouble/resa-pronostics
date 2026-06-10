"""Rappels automatiques (plan hybride, volet A: emails).

Boucle de fond qui, à intervalle régulier, envoie ce qui est dû :
- rappel J-1 aux participants avec des matchs du lendemain sans prono ;
- rappel pré-tournoi quand la deadline est à moins de 24 h ;
- rappel des questions bonus sans réponse à moins de 24 h de la deadline ;
- récap quotidien de la veille (points, rang, top 3).

Chaque envoi est journalisé dans notification_log (anti-doublons) et
respecte l'opt-in email du participant. Le volet B (notifications push)
se branche dans app.notify sans toucher à cette boucle.
"""
import asyncio
import logging
from datetime import datetime

from app.config import settings
from app.database import get_db
from app.notify import (
    notify_bonus_reminder,
    notify_daily_recap,
    notify_match_day_reminder,
    notify_pre_tournament_reminder,
)
from app.pre_tournament import (
    get_pre_tournament_deadline,
    get_pre_tournament_questions,
    pt_filled_keys,
)
from app.scoring import get_rank_evolution, get_rankings
from app.settings_store import knockout_predictions_open
from app.timeutils import (
    DISPLAY_TZ,
    local_today,
    now_utc,
    now_utc_iso,
    parse_utc_iso,
    utc_day_bounds_for_local_date,
)

logger = logging.getLogger(__name__)

# Heures locales (Brussels) d'envoi
MATCH_REMINDER_FROM_HOUR = 17   # rappel J-1 en fin d'après-midi
RECAP_FROM_HOUR = 9             # récap de la veille le matin
RECAP_FORCE_HOUR = 13           # envoyé même si tout n'est pas encodé


async def _already_sent(db, participant_id: int, kind: str, ref: str) -> bool:
    row = await db.execute(
        "SELECT 1 FROM notification_log WHERE participant_id=? AND kind=? AND ref=?",
        (participant_id, kind, ref),
    )
    return await row.fetchone() is not None


async def _mark_sent(db, participant_id: int, kind: str, ref: str):
    await db.execute(
        """INSERT INTO notification_log (participant_id, kind, ref)
           VALUES (?,?,?)
           ON CONFLICT(participant_id, kind, ref) DO NOTHING""",
        (participant_id, kind, ref),
    )


async def _opted_in_participants(db) -> list:
    rows = await db.execute(
        """SELECT * FROM participants
           WHERE is_confirmed=1 AND is_admin=0 AND email_opt_in=1"""
    )
    return [dict(r) for r in await rows.fetchall()]


async def _job_match_reminders(db, now_local: datetime):
    if now_local.hour < MATCH_REMINDER_FROM_HOUR:
        return
    tomorrow = local_today(1)
    ref = tomorrow.isoformat()
    start, end = utc_day_bounds_for_local_date(tomorrow)
    knockout_open = await knockout_predictions_open(db)
    rows = await db.execute(
        """SELECT * FROM matches
           WHERE datetime(match_date || 'T' || kickoff_time) >= datetime(?)
             AND datetime(match_date || 'T' || kickoff_time) <= datetime(?)
           ORDER BY match_date, kickoff_time""",
        (start, end),
    )
    matches = [dict(m) for m in await rows.fetchall()]
    matches = [m for m in matches if m["phase"] == "group" or knockout_open]
    if not matches:
        return
    match_ids = [m["id"] for m in matches]
    placeholders = ",".join("?" for _ in match_ids)
    for m in matches:
        kickoff = parse_utc_iso(f"{m['match_date']}T{m['kickoff_time']}")
        m["kickoff_local"] = kickoff.astimezone(DISPLAY_TZ).strftime("%H:%M")
    date_label = tomorrow.strftime("%d/%m")
    for p in await _opted_in_participants(db):
        if await _already_sent(db, p["id"], "match_reminder", ref):
            continue
        pred_rows = await db.execute(
            f"SELECT match_id FROM predictions WHERE participant_id=? AND match_id IN ({placeholders})",
            (p["id"], *match_ids),
        )
        predicted = {r["match_id"] for r in await pred_rows.fetchall()}
        missing = [m for m in matches if m["id"] not in predicted]
        if not missing:
            continue
        await notify_match_day_reminder(db, p, missing, date_label)
        await _mark_sent(db, p["id"], "match_reminder", ref)
    await db.commit()


async def _job_pre_tournament_reminder(db, now_iso: str):
    deadline = await get_pre_tournament_deadline(db)
    try:
        hours_left = (parse_utc_iso(deadline) - now_utc()).total_seconds() / 3600
    except Exception:
        return
    if not (0 < hours_left <= 24):
        return
    questions = await get_pre_tournament_questions(db)
    enabled_keys = [q["key"] for q in questions]
    for p in await _opted_in_participants(db):
        if await _already_sent(db, p["id"], "pt_reminder", deadline):
            continue
        row = await db.execute(
            "SELECT * FROM pre_tournament_predictions WHERE participant_id=?",
            (p["id"],),
        )
        pt = await row.fetchone()
        filled = pt_filled_keys(dict(pt) if pt else None, enabled_keys)
        if len(filled) >= len(enabled_keys):
            continue
        await notify_pre_tournament_reminder(db, p)
        await _mark_sent(db, p["id"], "pt_reminder", deadline)
    await db.commit()


async def _job_bonus_reminders(db, now_iso: str):
    rows = await db.execute(
        """SELECT * FROM bonus_questions
           WHERE deadline > ?
             AND datetime(deadline) <= datetime(?, '+24 hours')""",
        (now_iso, now_iso),
    )
    questions = [dict(q) for q in await rows.fetchall()]
    if not questions:
        return
    participants = await _opted_in_participants(db)
    for q in questions:
        ref = str(q["id"])
        deadline_label = (
            parse_utc_iso(q["deadline"]).astimezone(DISPLAY_TZ).strftime("%d/%m à %H:%M")
        )
        for p in participants:
            if await _already_sent(db, p["id"], "bonus_reminder", ref):
                continue
            ans_row = await db.execute(
                "SELECT 1 FROM bonus_answers WHERE participant_id=? AND question_id=?",
                (p["id"], q["id"]),
            )
            if await ans_row.fetchone():
                continue
            await notify_bonus_reminder(db, p, q, deadline_label)
            await _mark_sent(db, p["id"], "bonus_reminder", ref)
    await db.commit()


async def _job_daily_recap(db, now_local: datetime):
    if now_local.hour < RECAP_FROM_HOUR:
        return
    yesterday = local_today(-1)
    ref = yesterday.isoformat()
    start, end = utc_day_bounds_for_local_date(yesterday)
    counts_row = await db.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS encoded
           FROM matches
           WHERE datetime(match_date || 'T' || kickoff_time) >= datetime(?)
             AND datetime(match_date || 'T' || kickoff_time) <= datetime(?)""",
        (start, end),
    )
    counts = await counts_row.fetchone()
    total, encoded = counts["total"], counts["encoded"] or 0
    if total == 0 or encoded == 0:
        return
    if encoded < total and now_local.hour < RECAP_FORCE_HOUR:
        return  # attendre l'encodage complet jusqu'en début d'après-midi
    rankings = await get_rankings(db)
    evolution = await get_rank_evolution(db)
    rank_by_id = {r["id"]: r for r in rankings}
    top3 = [(r["name"], r["total_points"]) for r in rankings[:3]]
    pts_rows = await db.execute(
        """SELECT s.participant_id, COALESCE(SUM(s.points), 0) AS pts
           FROM scores s
           JOIN matches m ON m.id = s.match_id
           WHERE m.result IS NOT NULL
             AND datetime(m.match_date || 'T' || m.kickoff_time) >= datetime(?)
             AND datetime(m.match_date || 'T' || m.kickoff_time) <= datetime(?)
           GROUP BY s.participant_id""",
        (start, end),
    )
    points_by_id = {r["participant_id"]: r["pts"] for r in await pts_rows.fetchall()}
    date_label = yesterday.strftime("%d/%m")
    for p in await _opted_in_participants(db):
        if await _already_sent(db, p["id"], "daily_recap", ref):
            continue
        rank_data = rank_by_id.get(p["id"])
        if not rank_data:
            continue
        recap = {
            "date_label": date_label,
            "points": points_by_id.get(p["id"], 0),
            "match_count": encoded,
            "rank": rank_data["rank"],
            "evolution": evolution.get(p["id"]),
            "top3": top3,
        }
        await notify_daily_recap(db, p, recap)
        await _mark_sent(db, p["id"], "daily_recap", ref)
    await db.commit()


async def run_pending_notifications(now_local: datetime | None = None):
    """Une passe d'envoi. Idempotente: rejouable sans doublons."""
    if now_local is None:
        now_local = now_utc().astimezone(DISPLAY_TZ)
    now_iso = now_utc_iso()
    async with get_db() as db:
        await _job_pre_tournament_reminder(db, now_iso)
        await _job_bonus_reminders(db, now_iso)
        await _job_match_reminders(db, now_local)
        await _job_daily_recap(db, now_local)


async def scheduler_loop():
    logger.info(
        "Scheduler de rappels démarré (intervalle %ss).", settings.SCHEDULER_INTERVAL
    )
    await asyncio.sleep(15)  # laisser l'app finir de démarrer
    while True:
        try:
            await run_pending_notifications()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Échec d'une passe du scheduler de rappels")
        await asyncio.sleep(settings.SCHEDULER_INTERVAL)
