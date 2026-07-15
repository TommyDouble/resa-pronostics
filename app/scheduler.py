"""Rappels automatiques et captures planifiées.

Boucle de fond qui, à intervalle régulier, envoie ce qui est dû :
- rappel d'encodage matinal (9 h) des matchs jusqu'au lendemain 9 h ;
- escalade proche du coup d'envoi (~2 h puis ~1 h) pour les retardataires ;
- rappel pré-tournoi quand la deadline est à moins de 24 h ;
- rappel des questions bonus sans réponse à moins de 24 h de la deadline ;
- récap quotidien de la dernière journée sportive finalisée (points, rang, top 3).

Chaque envoi est journalisé dans notification_log (anti-doublons). Les
rappels d'encodage, bonus et le récap quotidien sont *push only* ; seuls
le pré-tournoi et (à venir) le récap hebdo gardent un repli email.
"""
import asyncio
import logging
from datetime import datetime, time, timedelta

from app.config import settings
from app.database import get_db
from app.knockout import match_predictions_open
from app.notify import (
    notify_bonus_reminder,
    notify_daily_recap,
    notify_encoding_escalation,
    notify_encoding_morning,
    notify_pre_tournament_reminder,
)
from app.pre_tournament import (
    get_pre_tournament_deadline,
    get_pre_tournament_questions,
    pt_filled_keys,
)
from app.scoring import (
    get_sporting_day_states,
    sync_finalized_ranking_update_history,
)
from app.timeutils import (
    DISPLAY_TZ,
    format_sporting_day_fr,
    local_today,
    match_kickoff_utc,
    minutes_until_match,
    now_utc,
    now_utc_iso,
    parse_utc_iso,
    utc_iso,
)

logger = logging.getLogger(__name__)

# Heures locales (Brussels) d'envoi
MORNING_REMINDER_HOUR = 9       # rappel d'encodage du matin
RECAP_FROM_HOUR = 9             # récap de la dernière journée finalisée, le matin
ESCALATION_2H_MINUTES = 120     # fenêtre haute de l'escalade
ESCALATION_1H_MINUTES = 60      # bascule en mode urgent


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
    """Participants joignables par email (opt-in respecté) — comms à fort enjeu."""
    rows = await db.execute(
        """SELECT * FROM participants
           WHERE is_confirmed=1 AND is_admin=0 AND email_opt_in=1"""
    )
    return [dict(r) for r in await rows.fetchall()]


async def _push_eligible_participants(db) -> list:
    """Participants avec un abonnement push actif — comms push only.

    NB: on ne filtre PAS sur email_opt_in (c'est un canal différent) : un
    participant peut couper l'email mais vouloir les notifications.
    """
    rows = await db.execute(
        """SELECT * FROM participants p
           WHERE p.is_confirmed=1 AND p.is_admin=0
             AND EXISTS (SELECT 1 FROM push_subscriptions ps
                         WHERE ps.participant_id = p.id)"""
    )
    return [dict(r) for r in await rows.fetchall()]


async def _unpredicted_match_ids(db, participant_id: int, match_ids: list) -> set:
    """Sous-ensemble de match_ids que ce participant n'a pas encore pronostiqués."""
    if not match_ids:
        return set()
    placeholders = ",".join("?" for _ in match_ids)
    rows = await db.execute(
        f"SELECT match_id FROM predictions WHERE participant_id=? AND match_id IN ({placeholders})",
        (participant_id, *match_ids),
    )
    predicted = {r["match_id"] for r in await rows.fetchall()}
    return {mid for mid in match_ids if mid not in predicted}


async def _gated_matches(db, start_iso: str, end_iso: str) -> list:
    """Matchs dont le coup d'envoi tombe dans [start, end], gating knockout inclus."""
    rows = await db.execute(
        """SELECT * FROM matches
           WHERE datetime(match_date || 'T' || kickoff_time) >= datetime(?)
             AND datetime(match_date || 'T' || kickoff_time) <= datetime(?)
           ORDER BY match_date, kickoff_time""",
        (start_iso, end_iso),
    )
    matches = [dict(m) for m in await rows.fetchall()]
    return [m for m in matches if match_predictions_open(m)]


async def _job_morning_encoding_reminder(db, now_local: datetime):
    """Rappel matinal (9 h, push only) : matchs à pronostiquer jusqu'au lendemain 9 h."""
    if now_local.hour != MORNING_REMINDER_HOUR:
        return
    today = local_today(0)
    ref = today.isoformat()
    # Fenêtre : de maintenant jusqu'au lendemain 9 h (heure locale).
    start_iso = now_utc_iso()
    window_end_local = datetime.combine(
        local_today(1), time(MORNING_REMINDER_HOUR, 0), tzinfo=DISPLAY_TZ
    )
    end_iso = utc_iso(window_end_local)
    matches = await _gated_matches(db, start_iso, end_iso)
    if not matches:
        return
    match_ids = [m["id"] for m in matches]
    for p in await _push_eligible_participants(db):
        if await _already_sent(db, p["id"], "enc_morning", ref):
            continue
        missing = await _unpredicted_match_ids(db, p["id"], match_ids)
        if not missing:
            continue
        await notify_encoding_morning(db, p, len(missing))
        await _mark_sent(db, p["id"], "enc_morning", ref)
    await db.commit()


async def _job_lock_escalation(db, now_local: datetime):
    """Escalade push only par match : ~1 h (urgent) sinon ~2 h avant le coup d'envoi.

    Un seul push par (participant, match, palier) grâce à notification_log.
    Le palier 1 h prime : une fois sous 60 min, on n'envoie plus le 2 h.
    """
    now_iso = now_utc_iso()
    horizon_local = now_utc().astimezone(DISPLAY_TZ) + timedelta(minutes=ESCALATION_2H_MINUTES)
    matches = await _gated_matches(db, now_iso, utc_iso(horizon_local))
    if not matches:
        return
    eligible = await _push_eligible_participants(db)
    for m in matches:
        mins = minutes_until_match(m)
        if mins <= 0 or mins > ESCALATION_2H_MINUTES:
            continue
        urgent = mins <= ESCALATION_1H_MINUTES
        kind = "enc_1h" if urgent else "enc_2h"
        ref = str(m["id"])
        for p in eligible:
            if await _already_sent(db, p["id"], kind, ref):
                continue
            missing = await _unpredicted_match_ids(db, p["id"], [m["id"]])
            if not missing:
                continue
            await notify_encoding_escalation(db, p, m, urgent)
            await _mark_sent(db, p["id"], kind, ref)
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
           WHERE is_published=1
             AND deadline > ?
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
    day_row = await db.execute(
        "SELECT MAX(update_day) AS day FROM ranking_update_day_evolutions"
    )
    ref = (await day_row.fetchone())["day"]
    use_update_day = bool(ref)
    if not ref:
        day_row = await db.execute(
            "SELECT MAX(sporting_day) AS day FROM sporting_day_rank_evolutions"
        )
        ref = (await day_row.fetchone())["day"]
    if not ref:
        return
    if use_update_day:
        evo_rows = await db.execute(
            """SELECT e.*, COALESCE(NULLIF(p.nickname, ''), p.name) AS display_name
               FROM ranking_update_day_evolutions e
               JOIN participants p ON p.id=e.participant_id
               WHERE e.update_day=?
               ORDER BY e.rank_after, e.points_after DESC, display_name""",
            (ref,),
        )
    else:
        evo_rows = await db.execute(
            """SELECT e.*, COALESCE(NULLIF(p.nickname, ''), p.name) AS display_name
               FROM sporting_day_rank_evolutions e
               JOIN participants p ON p.id=e.participant_id
               WHERE e.sporting_day=?
               ORDER BY e.rank_after, e.points_after DESC, display_name""",
            (ref,),
        )
    evolution_rows = [dict(r) for r in await evo_rows.fetchall()]
    if not evolution_rows:
        return
    evolution_by_id = {r["participant_id"]: r for r in evolution_rows}
    top3 = [(r["display_name"], r["points_after"]) for r in evolution_rows[:3]]
    states = await get_sporting_day_states(db)
    match_count = states.get(ref, {}).get("match_count", 0)
    date_label = format_sporting_day_fr(ref)
    for p in await _opted_in_participants(db):
        if await _already_sent(db, p["id"], "daily_recap", ref):
            continue
        rank_data = evolution_by_id.get(p["id"])
        if not rank_data:
            continue
        recap = {
            "date_label": date_label,
            "points": rank_data["day_points"],
            "match_count": match_count,
            "rank": rank_data["rank_after"],
            "evolution": rank_data["delta"],
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
        finalized = await sync_finalized_ranking_update_history(db)
        if finalized:
            from app.trophies import refresh_trophy_awards
            await refresh_trophy_awards(db)
            await db.commit()
        await _job_pre_tournament_reminder(db, now_iso)
        await _job_bonus_reminders(db, now_iso)
        await _job_morning_encoding_reminder(db, now_local)
        await _job_lock_escalation(db, now_local)
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
