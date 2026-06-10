"""JSON API endpoints called by the frontend JS."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth import get_participant_by_token
from app.database import get_db
from app.settings_store import knockout_predictions_open
from app.timeutils import is_match_locked

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
