"""Canal de notification unifié (plan hybride).

Pour chaque rappel: notification push si le participant a un abonnement
actif. L'email n'est conservé QUE comme filet de sécurité des comms à
fort enjeu (deadline pré-tournoi, récap hebdo). Les rappels répétitifs
(encodage, bonus, récap quotidien) sont *push only*: passer ``send_email``
à ``None`` désactive tout repli email et évite la sensation de spam.
"""
import logging

from app import mail
from app.config import settings

logger = logging.getLogger(__name__)


async def _push_or_email(db, participant: dict, title: str, body: str,
                         url_path: str, send_email=None) -> None:
    """Envoie un push; replie sur l'email seulement si ``send_email`` est fourni.

    ``send_email=None`` ⇒ push-only (aucun email, même sans abonnement push).
    """
    from app.push import send_push_to_participant  # import tardif (dépendance optionnelle)

    delivered = await send_push_to_participant(
        db, participant["id"], title=title, body=body,
        url=f"{settings.BASE_URL}{url_path}",
    )
    if not delivered and send_email is not None:
        await send_email()


async def notify_encoding_morning(db, participant: dict, count: int):
    """Rappel matinal (push only): pronos à saisir avant le lendemain 9 h."""
    s = "s" if count > 1 else ""
    await _push_or_email(
        db, participant,
        title=f"⚽ {count} match{s} à pronostiquer",
        body="Complète tes scores avant les coups d'envoi.",
        url_path=f"/p/{participant['token']}/pronos",
    )


async def notify_encoding_escalation(db, participant: dict, match: dict, urgent: bool):
    """Escalade proche du coup d'envoi (push only): ~1 h (urgent) ou ~2 h."""
    label = f"{match['team1_name']} – {match['team2_name']}"
    title = (
        "🚨 Dernier appel — coup d'envoi dans 1 h"
        if urgent else "⏰ Coup d'envoi dans ~2 h"
    )
    await _push_or_email(
        db, participant,
        title=title,
        body=f"{label} : ton prono n'est pas encore enregistré.",
        url_path=f"/p/{participant['token']}/pronos?match={match['id']}#match-{match['id']}",
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
        # push only : pas de repli email pour un rappel récurrent.
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
        title=f"📊 {recap['date_label'].capitalize()}: +{recap['points']} pts{evo}",
        body=f"Tu es {recap['rank']}e au général. Top 3: "
             + ", ".join(name for name, _ in recap.get("top3", [])[:3]),
        url_path=f"/p/{participant['token']}/classement",
        # push only : pas de repli email pour le récap quotidien.
    )
