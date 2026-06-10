"""Canal de notification unifié (plan hybride).

Pour chaque rappel: notification push si le participant a un abonnement
actif (volet B), sinon email (volet A). L'email reste le filet de
sécurité; le scheduler n'a pas à connaître le canal utilisé.
"""
import logging

from app import mail
from app.config import settings

logger = logging.getLogger(__name__)


async def _push_or_email(db, participant: dict, title: str, body: str,
                         url_path: str, send_email) -> None:
    from app.push import send_push_to_participant  # import tardif (dépendance optionnelle)

    delivered = await send_push_to_participant(
        db, participant["id"], title=title, body=body,
        url=f"{settings.BASE_URL}{url_path}",
    )
    if not delivered:
        await send_email()


async def notify_match_day_reminder(db, participant: dict, matches: list, date_label: str):
    count = len(matches)
    await _push_or_email(
        db, participant,
        title=f"⚽ {count} match{'s' if count > 1 else ''} demain",
        body="Il te reste des scores à compléter avant les coups d'envoi.",
        url_path=f"/p/{participant['token']}/pronos",
        send_email=lambda: mail.send_match_day_reminder(participant, matches, date_label),
    )


async def notify_pre_tournament_reminder(db, participant: dict):
    await _push_or_email(
        db, participant,
        title="🏆 Pré-tournoi: dernière ligne droite",
        body="Champion, finalistes, buteur… la deadline est à moins de 24 h.",
        url_path=f"/p/{participant['token']}/pre-tournoi",
        send_email=lambda: mail.send_pre_tournament_reminder(participant),
    )


async def notify_bonus_reminder(db, participant: dict, question: dict, deadline_label: str):
    await _push_or_email(
        db, participant,
        title=f"⭐ Question bonus ({question['points_value']} pts)",
        body=f"Sans réponse de ta part — deadline {deadline_label}.",
        url_path=f"/p/{participant['token']}/bonus",
        send_email=lambda: mail.send_bonus_reminder(participant, question, deadline_label),
    )


async def notify_daily_recap(db, participant: dict, recap: dict):
    evolution = recap.get("evolution")
    if evolution and evolution > 0:
        evo = f" · ▲{evolution} place{'s' if evolution > 1 else ''}"
    elif evolution and evolution < 0:
        evo = f" · ▼{-evolution}"
    else:
        evo = ""
    await _push_or_email(
        db, participant,
        title=f"📊 Hier: +{recap['points']} pts{evo}",
        body=f"Tu es {recap['rank']}e au général. Top 3: "
             + ", ".join(name for name, _ in recap.get("top3", [])[:3]),
        url_path=f"/p/{participant['token']}/classement",
        send_email=lambda: mail.send_daily_recap(participant, recap),
    )
