"""JSON API endpoints called by the frontend JS."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth import get_participant_by_token
from app.config import settings
from app.database import get_db
from app.push import push_enabled
from app.settings_store import knockout_predictions_open
from app.timeutils import is_match_locked, match_live_state

router = APIRouter()
logger = logging.getLogger(__name__)


def _is_locked(match: dict) -> bool:
    return is_match_locked(match)


class PredictionIn(BaseModel):
    match_id: int
    prediction: Optional[str] = None
    exact_score_team1: Optional[int] = None
    exact_score_team2: Optional[int] = None
    qualifier_prediction: Optional[str] = None


def _validate_score(score: Optional[int], label: str) -> int:
    if score is None:
        raise HTTPException(400, f"Score {label} requis")
    if score < 0 or score > 30:
        raise HTTPException(400, "Le score doit être compris entre 0 et 30")
    return score


def _prediction_from_score(score_team1: int, score_team2: int) -> str:
    if score_team1 > score_team2:
        return "team1"
    if score_team2 > score_team1:
        return "team2"
    return "draw"


@router.post("/predictions")
async def submit_prediction(body: PredictionIn, token: str = Query(...)):
    p = await get_participant_by_token(token)
    if not p:
        raise HTTPException(403, "Token invalide")
    score_team1 = _validate_score(body.exact_score_team1, "équipe 1")
    score_team2 = _validate_score(body.exact_score_team2, "équipe 2")
    if body.qualifier_prediction is not None and body.qualifier_prediction not in ("team1", "team2"):
        raise HTTPException(400, "Qualifié invalide")
    async with get_db() as db:
        match_row = await db.execute("SELECT * FROM matches WHERE id=?", (body.match_id,))
        match = await match_row.fetchone()
        if not match:
            raise HTTPException(404, "Match introuvable")
        if _is_locked(dict(match)):
            raise HTTPException(403, "Ce match est verrouillé")
        is_knockout = match["phase"] != "group"
        if is_knockout and not await knockout_predictions_open(db):
            raise HTTPException(403, "Les pronostics de phase finale ne sont pas encore ouverts.")
        prediction = _prediction_from_score(score_team1, score_team2)
        qualifier_prediction = None
        if is_knockout and prediction == "draw":
            if body.qualifier_prediction not in ("team1", "team2"):
                raise HTTPException(400, "Choisis l'équipe qualifiée")
            qualifier_prediction = body.qualifier_prediction
        await db.execute(
            """INSERT INTO predictions (participant_id, match_id, prediction,
                 exact_score_team1, exact_score_team2, qualifier_prediction)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(participant_id, match_id) DO UPDATE SET
                 prediction=excluded.prediction,
                 exact_score_team1=excluded.exact_score_team1,
                 exact_score_team2=excluded.exact_score_team2,
                 qualifier_prediction=excluded.qualifier_prediction,
                 submitted_at=datetime('now')""",
            (p["id"], body.match_id, prediction, score_team1, score_team2, qualifier_prediction)
        )
        await db.commit()
    return {
        "success": True,
        "message": "Enregistré",
        "prediction": prediction,
        "qualifier_prediction": qualifier_prediction,
    }


@router.get("/match/{match_id}/status")
async def match_status(match_id: int, token: str = Query(...)):
    """État live d'un match, pollé par la page de détail pendant un match."""
    p = await get_participant_by_token(token)
    if not p:
        raise HTTPException(403, "Token invalide")
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
    if not match:
        raise HTTPException(404, "Match introuvable")
    match = dict(match)
    return {
        "state": match_live_state(match),
        "score_team1": match.get("score_team1"),
        "score_team2": match.get("score_team2"),
        "qualifier_winner": match.get("qualifier_winner"),
    }


# ---- Story des nouveautés ----

class NewsSeenIn(BaseModel):
    id: int


@router.post("/news/seen")
async def news_seen(body: NewsSeenIn, token: str = Query(...)):
    """Marque les nouveautés vues jusqu'à l'id donné (monotone, jamais en arrière)."""
    p = await get_participant_by_token(token)
    if not p:
        raise HTTPException(403, "Token invalide")
    async with get_db() as db:
        await db.execute(
            """UPDATE participants
               SET last_seen_news_id = MAX(COALESCE(last_seen_news_id, 0), ?)
               WHERE id = ?""",
            (body.id, p["id"]),
        )
        await db.commit()
    return {"success": True}


@router.post("/reveal/seen")
async def reveal_seen(token: str = Query(...), sporting_day: str = Query(default="")):
    """Marque le reveal comme vu : avance last_revealed_date à la journée
    sportive la plus récente du périmètre (recalculé serveur, pas de valeur client)."""
    p = await get_participant_by_token(token)
    if not p:
        raise HTTPException(403, "Token invalide")
    from app.routers.pages import _reveal_window_data
    from app.scoring import get_sporting_day_states
    async with get_db() as db:
        reveal = await _reveal_window_data(db, p["id"])
        if reveal:
            states = await get_sporting_day_states(db)
            requested_is_finalized = bool(
                sporting_day and states.get(sporting_day, {}).get("finalized")
            )
            revealed_through = sporting_day if requested_is_finalized else reveal["sporting_day"]
            await db.execute(
                """UPDATE participants
                   SET last_revealed_date=MAX(COALESCE(last_revealed_date, ''), ?)
                   WHERE id=?""",
                (revealed_through, p["id"]),
            )
            await db.commit()
    return {"success": True}


# ---- Notifications push (volet B) ----

class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: dict


class PushUnsubscribeIn(BaseModel):
    endpoint: str


@router.get("/push/config")
async def push_config(token: str = Query(...)):
    p = await get_participant_by_token(token)
    if not p:
        raise HTTPException(403, "Token invalide")
    return {"enabled": push_enabled(), "publicKey": settings.VAPID_PUBLIC_KEY}


@router.post("/push/subscribe")
async def push_subscribe(body: PushSubscriptionIn, token: str = Query(...)):
    p = await get_participant_by_token(token)
    if not p:
        raise HTTPException(403, "Token invalide")
    p256dh = (body.keys or {}).get("p256dh", "")
    auth = (body.keys or {}).get("auth", "")
    if not body.endpoint or not p256dh or not auth:
        raise HTTPException(400, "Abonnement incomplet")
    async with get_db() as db:
        await db.execute(
            """INSERT INTO push_subscriptions (participant_id, endpoint, p256dh, auth)
               VALUES (?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 participant_id=excluded.participant_id,
                 p256dh=excluded.p256dh,
                 auth=excluded.auth""",
            (p["id"], body.endpoint, p256dh, auth),
        )
        await db.commit()
    return {"success": True}


@router.post("/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubscribeIn, token: str = Query(...)):
    p = await get_participant_by_token(token)
    if not p:
        raise HTTPException(403, "Token invalide")
    async with get_db() as db:
        await db.execute(
            "DELETE FROM push_subscriptions WHERE endpoint=? AND participant_id=?",
            (body.endpoint, p["id"]),
        )
        await db.commit()
    return {"success": True}
