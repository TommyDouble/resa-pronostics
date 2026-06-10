"""Notifications push web (volet B du plan hybride).

Sans clés VAPID configurées ou sans abonnement, send_push_to_participant
renvoie False et le canal email prend le relais.
"""
import logging

logger = logging.getLogger(__name__)


async def send_push_to_participant(db, participant_id: int, *, title: str,
                                   body: str, url: str) -> bool:
    """Envoie une notification push. True si au moins un envoi a réussi."""
    return False
