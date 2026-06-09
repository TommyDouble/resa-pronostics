"""Score calculation logic per spec."""
from app.database import get_db

PRE_TOURNAMENT_POINTS = {
    "winner": 8,
    "finalist": 5,
    "top_scorer": 5,
    "revelation": 5,
}


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
        COALESCE(sa.score_points, 0) + COALESCE(pta.pre_tournament_points, 0) as total_points,
        COALESCE(sa.score_points, 0) as match_bonus_points,
        COALESCE(pta.pre_tournament_points, 0) as pre_tournament_points,
        COALESCE(sa.matches_scored, 0) as matches_scored,
        COALESCE(pm.exact_count, 0) as exact_count,
        COALESCE(pm.outcome_count, 0) as outcome_count
    FROM participants p
    LEFT JOIN (
        SELECT
            participant_id,
            SUM(points) as score_points,
            COUNT(DISTINCT CASE WHEN match_id IS NOT NULL THEN match_id END) as matches_scored
        FROM scores
        GROUP BY participant_id
    ) sa ON sa.participant_id = p.id
    LEFT JOIN (
        SELECT participant_id, SUM(points) as pre_tournament_points
        FROM pre_tournament_scores
        GROUP BY participant_id
    ) pta ON pta.participant_id = p.id
    LEFT JOIN (
        SELECT
            pr.participant_id,
            SUM(CASE WHEN m.result IS NOT NULL AND pr.prediction = m.result THEN 1 ELSE 0 END) as outcome_count,
            SUM(CASE WHEN m.result IS NOT NULL
                      AND pr.exact_score_team1 = m.score_team1
                      AND pr.exact_score_team2 = m.score_team2
                      AND pr.exact_score_team1 IS NOT NULL
                     THEN 1 ELSE 0 END) as exact_count
        FROM predictions pr
        JOIN matches m ON m.id = pr.match_id
        GROUP BY pr.participant_id
    ) pm ON pm.participant_id = p.id
    WHERE p.is_confirmed = 1 AND p.is_admin = 0 AND p.is_active = 1
    ORDER BY
        total_points DESC,
        exact_count DESC,
        outcome_count DESC,
        COALESCE(NULLIF(p.nickname, ''), p.name) ASC
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
            "match_bonus_points": p["match_bonus_points"],
            "pre_tournament_points": p["pre_tournament_points"],
            "matches_scored": p["matches_scored"],
            "exact_count": p["exact_count"],
            "outcome_count": p["outcome_count"],
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


def _norm(value) -> str:
    return str(value or "").strip().casefold()


def calculate_pre_tournament_points(question_key: str, answer, official_answer) -> int:
    """Return points for one pre-tournament answer."""
    if official_answer in (None, "") or answer in (None, ""):
        return 0
    if question_key == "total_goals":
        try:
            predicted = int(answer)
            official = int(official_answer)
        except (TypeError, ValueError):
            return 0
        if predicted == official:
            return 8
        if abs(predicted - official) <= 3:
            return 4
        return 0
    return PRE_TOURNAMENT_POINTS.get(question_key, 0) if _norm(answer) == _norm(official_answer) else 0


async def recalculate_pre_tournament_scores():
    """Recalculate all pre-tournament scores from official answers."""
    async with get_db() as db:
        official_rows = await db.execute(
            "SELECT key, answer FROM pre_tournament_official_answers"
        )
        official = {
            r["key"]: r["answer"]
            for r in await official_rows.fetchall()
            if r["answer"] not in (None, "")
        }
        question_rows = await db.execute(
            "SELECT key FROM pre_tournament_questions WHERE is_enabled = 1"
        )
        enabled = {r["key"] for r in await question_rows.fetchall()}
        rows = await db.execute(
            """SELECT pt.*
               FROM pre_tournament_predictions pt
               JOIN participants p ON p.id = pt.participant_id
               WHERE pt.submitted = 1 AND p.is_admin = 0"""
        )
        predictions = await rows.fetchall()

        await db.execute("DELETE FROM pre_tournament_scores")
        for pred in predictions:
            pred_dict = dict(pred)
            for key in enabled:
                if key not in official:
                    continue
                points = calculate_pre_tournament_points(
                    key,
                    pred_dict.get(key),
                    official[key],
                )
                await db.execute(
                    """INSERT INTO pre_tournament_scores
                       (participant_id, question_key, points)
                       VALUES (?, ?, ?)""",
                    (pred_dict["participant_id"], key, points),
                )
        await db.commit()
