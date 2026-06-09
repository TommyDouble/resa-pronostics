"""JSON API endpoints called by the frontend JS."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.auth import get_participant_by_token
from app.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _is_locked(match: dict) -> bool:
    try:
        kickoff = datetime.fromisoformat(
            f"{match['match_date']}T{match['kickoff_time']}"
        ).replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= kickoff
    except Exception:
        return False


def _score_outcome(score_team1: int, score_team2: int) -> str:
    if score_team1 > score_team2:
        return "team1"
    if score_team2 > score_team1:
        return "team2"
    return "draw"


def _validate_exact_score_consistency(body: "PredictionIn"):
    has_one_score = body.exact_score_team1 is not None or body.exact_score_team2 is not None
    has_both_scores = body.exact_score_team1 is not None and body.exact_score_team2 is not None
    if has_one_score and not has_both_scores:
        raise HTTPException(400, "Score exact incomplet")
    if has_both_scores:
        if body.exact_score_team1 < 0 or body.exact_score_team2 < 0:
            raise HTTPException(400, "Score exact invalide")
        score_prediction = _score_outcome(body.exact_score_team1, body.exact_score_team2)
        if score_prediction != body.prediction:
            raise HTTPException(400, "Le score exact ne correspond pas au pronostic choisi")


class PredictionIn(BaseModel):
    match_id: int
    prediction: str
    exact_score_team1: Optional[int] = None
    exact_score_team2: Optional[int] = None


@router.post("/predictions")
async def submit_prediction(body: PredictionIn, token: str = Query(...)):
    if body.prediction not in ("team1", "draw", "team2"):
        raise HTTPException(400, "Prediction invalide")
    _validate_exact_score_consistency(body)
    p = await get_participant_by_token(token)
    if not p:
        raise HTTPException(403, "Token invalide")
    async with get_db() as db:
        match_row = await db.execute("SELECT * FROM matches WHERE id=?", (body.match_id,))
        match = await match_row.fetchone()
        if not match:
            raise HTTPException(404, "Match introuvable")
        if _is_locked(dict(match)):
            raise HTTPException(403, "Ce match est verrouillé")
        await db.execute(
            """INSERT INTO predictions (participant_id, match_id, prediction,
                 exact_score_team1, exact_score_team2)
               VALUES (?,?,?,?,?)
               ON CONFLICT(participant_id, match_id) DO UPDATE SET
                 prediction=excluded.prediction,
                 exact_score_team1=excluded.exact_score_team1,
                 exact_score_team2=excluded.exact_score_team2,
                 submitted_at=datetime('now')""",
            (p["id"], body.match_id, body.prediction,
             body.exact_score_team1, body.exact_score_team2)
        )
        await db.commit()
    return {"success": True, "message": "Enregistré"}
