"""Score calculation logic per spec."""
from app.database import get_db


def calculate_match_score(prediction: dict, match: dict) -> int:
    """Calculate points for a single prediction against a match result."""
    if match["result"] is None:
        return 0
    has_correct_outcome = prediction["prediction"] == match["result"]
    base = 2 if has_correct_outcome else 0
    exact = 0
    if has_correct_outcome and (
        prediction["exact_score_team1"] == match["score_team1"]
        and prediction["exact_score_team2"] == match["score_team2"]
        and prediction["exact_score_team1"] is not None
    ):
        exact = 2
    return base * match["weight"] + exact


async def recalculate_match_scores(match_id: int):
    """Delete and recalculate all scores for a match after result entry."""
    async with get_db() as db:
        # Get match result
        row = await db.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        match = await row.fetchone()
        if not match or match["result"] is None:
            return

        match_dict = dict(match)

        # Get all predictions for this match
        rows = await db.execute(
            "SELECT * FROM predictions WHERE match_id = ?", (match_id,)
        )
        predictions = await rows.fetchall()

        # Delete existing scores for this match
        await db.execute("DELETE FROM scores WHERE match_id = ?", (match_id,))

        # Insert new scores
        for pred in predictions:
            pred_dict = dict(pred)
            points = calculate_match_score(pred_dict, match_dict)
            await db.execute(
                """INSERT INTO scores (participant_id, match_id, points)
                   VALUES (?, ?, ?)""",
                (pred_dict["participant_id"], match_id, points),
            )

        await db.commit()


_RANKINGS_SQL = """
    SELECT
        p.id,
        p.name,
        p.nickname,
        p.email,
        COALESCE(SUM(s.points), 0) as total_points,
        COUNT(DISTINCT CASE WHEN s.match_id IS NOT NULL THEN s.match_id END) as matches_scored
    FROM participants p
    LEFT JOIN scores s ON s.participant_id = p.id
    WHERE p.is_confirmed = 1 AND p.is_admin = 0
    GROUP BY p.id, p.name, p.nickname, p.email
    ORDER BY total_points DESC, COALESCE(NULLIF(p.nickname, ''), p.name) ASC
"""


async def _rankings_from_db(db) -> list:
    rows = await db.execute(_RANKINGS_SQL)
    participants = await rows.fetchall()
    return [
        {
            "full_name": p["name"],
            "rank": i + 1,
            "id": p["id"],
            "name": p["nickname"] or p["name"],
            "nickname": p["nickname"],
            "email": p["email"],
            "total_points": p["total_points"],
            "matches_scored": p["matches_scored"],
        }
        for i, p in enumerate(participants)
    ]


async def get_rankings(db=None) -> list:
    """Return ranked list of participants with total scores."""
    if db is not None:
        return await _rankings_from_db(db)
    async with get_db() as db:
        return await _rankings_from_db(db)


async def calculate_bonus_scores(question_id: int):
    """Calculate scores for a bonus question after correct answer is set."""
    async with get_db() as db:
        row = await db.execute(
            "SELECT * FROM bonus_questions WHERE id = ?", (question_id,)
        )
        question = await row.fetchone()
        if not question or question["correct_answer"] is None:
            return

        rows = await db.execute(
            "SELECT * FROM bonus_answers WHERE question_id = ?", (question_id,)
        )
        answers = await rows.fetchall()

        await db.execute(
            "DELETE FROM scores WHERE bonus_question_id = ?", (question_id,)
        )

        for ans in answers:
            points = question["points_value"] if ans["answer"] == question["correct_answer"] else 0
            await db.execute(
                """INSERT INTO scores (participant_id, bonus_question_id, points)
                   VALUES (?, ?, ?)""",
                (ans["participant_id"], question_id, points),
            )

        await db.commit()
