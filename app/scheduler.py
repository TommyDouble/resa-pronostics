"""Rappels automatiques et captures planifiées.

Boucle de fond qui, à intervalle régulier, envoie ce qui est dû :
- rappel d'encodage matinal (9 h) des matchs jusqu'au lendemain 9 h ;
- escalade proche du coup d'envoi (~2 h puis ~1 h) pour les retardataires ;
- rappel pré-tournoi quand la deadline est à moins de 24 h ;
- rappel des questions bonus sans réponse à moins de 24 h de la deadline ;
- récap quotidien de la veille (points, rang, top 3) ;
- capture quotidienne du classement (ranking_snapshots) pour l'historique.

Chaque envoi est journalisé dans notification_log (anti-doublons). Les
rappels d'encodage, bonus et le récap quotidien sont *push only* ; seuls
le pré-tournoi et (à venir) le récap hebdo gardent un repli email.
"""
import asyncio
import logging
from datetime import datetime, time, timedelta

from app.config import settings
from app.database import get_db
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
from app.scoring import get_rank_evolution, get_rankings
from app.settings_store import knockout_predictions_open
from app.timeutils import (
    DISPLAY_TZ,
    local_today,
    match_kickoff_utc,
    minutes_until_match,
    now_utc,
    now_utc_iso,
    parse_utc_iso,
    utc_day_bounds_for_local_date,
    utc_iso,
)

logger = logging.getLogger(__name__)

# Heures locales (Brussels) d'envoi
MORNING_REMINDER_HOUR = 9       # rappel d'encodage du matin
RECAP_FROM_HOUR = 9             # récap de la veille le matin
RECAP_FORCE_HOUR = 13           # envoyé / capturé même si tout n'est pas encodé
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
    knockout_open = await knockout_predictions_open(db)
    rows = await db.execute(
        """SELECT * FROM matches
           WHERE datetime(match_date || 'T' || kickoff_time) >= datetime(?)
             AND datetime(match_date || 'T' || kickoff_time) <= datetime(?)
           ORDER BY match_date, kickoff_time""",
        (start_iso, end_iso),
    )
    matches = [dict(m) for m in await rows.fetchall()]
    return [m for m in matches if m["phase"] == "group" or knockout_open]


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


async def _job_capture_snapshot(db, now_local: datetime):
    """Capture le classement de fin de journée sportive dans ranking_snapshots.

    Idempotent et forward-only : une ligne par participant et par journée
    (snapshot_date = date locale de la veille). On n'écrit qu'une fois la
    journée stabilisée (tous les résultats encodés, ou après 13 h), comme le
    récap, afin d'absorber les encodages tardifs.
    """
    if now_local.hour < RECAP_FROM_HOUR:
        return
    yesterday = local_today(-1)
    snapshot_date = yesterday.isoformat()
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
        return  # journée pas encore stabilisée : attendre l'encodage complet
    rankings = await get_rankings(db)
    for r in rankings:
        await db.execute(
            """INSERT INTO ranking_snapshots (snapshot_date, participant_id, rank, total_points)
               VALUES (?,?,?,?)
               ON CONFLICT(snapshot_date, participant_id) DO NOTHING""",
            (snapshot_date, r["id"], r["rank"], r["total_points"]),
        )
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
    evolution = (await get_rank_evolution(db))["deltas"]
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
        await _job_morning_encoding_reminder(db, now_local)
        await _job_lock_escalation(db, now_local)
        await _job_daily_recap(db, now_local)
        await _job_capture_snapshot(db, now_local)


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
