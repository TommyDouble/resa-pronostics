"""Notifications push web (volet B du plan hybride).

Sans clés VAPID configurées ou sans abonnement actif, l'envoi renvoie
False et le canal email (app.notify) prend automatiquement le relais.
Les abonnements expirés (404/410) sont purgés au premier envoi raté.
"""
import asyncio
import json
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def push_enabled() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


async def send_push_to_participant(db, participant_id: int, *, title: str,
                                   body: str, url: str) -> bool:
    """Envoie une notification push. True si au moins un envoi a réussi."""
    if not push_enabled():
        return False
    rows = await db.execute(
        "SELECT * FROM push_subscriptions WHERE participant_id=?", (participant_id,)
    )
    subscriptions = [dict(r) for r in await rows.fetchall()]
    if not subscriptions:
        return False

    from pywebpush import WebPushException, webpush

    payload = json.dumps({"title": title, "body": body, "url": url})
    delivered = False
    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{settings.VAPID_CLAIMS_EMAIL}"},
            )
            delivered = True
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                # Abonnement mort (app désinstallée, permission révoquée…)
                await db.execute(
                    "DELETE FROM push_subscriptions WHERE id=?", (sub["id"],)
                )
                await db.commit()
                logger.info("Abonnement push expiré purgé (participant %s)", participant_id)
            else:
                logger.warning("Échec push participant %s: %s", participant_id, exc)
        except Exception:
            logger.exception("Erreur push participant %s", participant_id)
    return delivered
