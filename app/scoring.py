"""Score calculation logic per spec."""
import json

from app.database import get_db
from app.timeutils import DISPLAY_TZ, now_utc


def parse_revelation_winners(correct_answer) -> set:
    """Winning outsiders for the révélation question.

    Stored as a JSON list (new format, supports ties → several winning teams).
    Falls back to a single team string for legacy answers. Returns a set of
    non-empty team names.
    """
    if not correct_answer:
        return set()
    raw = str(correct_answer).strip()
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {raw}
    if isinstance(parsed, list):
        return {str(t).strip() for t in parsed if str(t).strip()}
    if isinstance(parsed, str) and parsed.strip():
        return {parsed.strip()}
    return set()


def _winner_from_scores(score_team1, score_team2) -> str:
    if score_team1 is None or score_team2 is None:
        return ""
    if score_team1 > score_team2:
        return "team1"
    if score_team2 > score_team1:
        return "team2"
    return "draw"


def _is_knockout(match: dict) -> bool:
    return match.get("phase") != "group"


def actual_match_winner(match: dict) -> str:
    """Return the actual winner for scoring.

    Group-stage matches keep the 90-minute result. Knockout matches use the
    qualified team; if the 90-minute score is not tied, the score itself gives it.
    """
    if not _is_knockout(match):
        return match.get("result") or ""
    winner = _winner_from_scores(match.get("score_team1"), match.get("score_team2"))
    if winner in ("team1", "team2"):
        return winner
    return match.get("qualifier_winner") or ""


def predicted_match_winner(prediction: dict, match: dict) -> str:
    """Return the participant's predicted winner for scoring."""
    if not _is_knockout(match):
        return prediction.get("prediction") or ""
    winner = _winner_from_scores(
        prediction.get("exact_score_team1"),
        prediction.get("exact_score_team2"),
    )
    if winner in ("team1", "team2"):
        return winner
    return prediction.get("qualifier_prediction") or ""


def is_match_prediction_correct(prediction: dict, match: dict) -> bool:
    """Whether a prediction gets the base outcome/winner points."""
    if match["result"] is None:
        return False
    actual = actual_match_winner(match)
    predicted = predicted_match_winner(prediction, match)
    return bool(actual) and bool(predicted) and predicted == actual


def is_match_score_exact(prediction: dict, match: dict) -> bool:
    """Whether a prediction gets the exact-score bonus."""
    return (
        is_match_prediction_correct(prediction, match)
        and prediction["exact_score_team1"] == match["score_team1"]
        and prediction["exact_score_team2"] == match["score_team2"]
        and prediction["exact_score_team1"] is not None
    )


def calculate_match_score(prediction: dict, match: dict) -> int:
    """Calculate points for a single prediction against a match result."""
    if match["result"] is None:
        return 0
    has_correct_outcome = is_match_prediction_correct(prediction, match)
    base = 2 if has_correct_outcome else 0
    exact = 2 if is_match_score_exact(prediction, match) else 0
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

        await record_ranking_snapshot(db)
        await db.commit()


# Expression SQL des points par périmètre de classement.
_SCOPE_POINTS = {
    "general": """
        COALESCE((SELECT SUM(s.points) FROM scores s WHERE s.participant_id = p.id), 0)
          + COALESCE((SELECT SUM(ps.points) FROM pre_tournament_scores ps WHERE ps.participant_id = p.id), 0)
    """,
    "groups": """
        COALESCE((SELECT SUM(s.points) FROM scores s
                  JOIN matches m ON m.id = s.match_id
                  WHERE s.participant_id = p.id AND m.phase = 'group'), 0)
    """,
    "knockout": """
        COALESCE((SELECT SUM(s.points) FROM scores s
                  JOIN matches m ON m.id = s.match_id
                  WHERE s.participant_id = p.id AND m.phase != 'group'), 0)
    """,
    "bonus": """
        COALESCE((SELECT SUM(s.points) FROM scores s
                  WHERE s.participant_id = p.id AND s.bonus_question_id IS NOT NULL), 0)
          + COALESCE((SELECT SUM(ps.points) FROM pre_tournament_scores ps WHERE ps.participant_id = p.id), 0)
    """,
    # Classement « fin de phase de groupes » servant de base à la remontada:
    # matchs de groupes + pré-tournoi + bonus publiés avant la phase finale.
    "groups_baseline": """
        COALESCE((SELECT SUM(s.points) FROM scores s
                  JOIN matches m ON m.id = s.match_id
                  WHERE s.participant_id = p.id AND m.phase = 'group'), 0)
          + COALESCE((SELECT SUM(s.points) FROM scores s
                      JOIN bonus_questions bq ON bq.id = s.bonus_question_id
                      WHERE s.participant_id = p.id AND bq.phase = 'pre_tournament'), 0)
          + COALESCE((SELECT SUM(ps.points) FROM pre_tournament_scores ps WHERE ps.participant_id = p.id), 0)
    """,
}

RANKING_SCOPES = ("general", "groups", "knockout", "bonus")


def _rankings_sql(scope: str) -> str:
    points_expr = _SCOPE_POINTS[scope]
    return f"""
    SELECT
        p.id,
        p.name,
        p.nickname,
        p.email,
        p.avatar_path,
        p.department,
        {points_expr} as total_points,
        (SELECT COUNT(DISTINCT s.match_id) FROM scores s
         WHERE s.participant_id = p.id AND s.match_id IS NOT NULL) as matches_scored
    FROM participants p
    WHERE p.is_confirmed = 1 AND p.is_admin = 0
    ORDER BY total_points DESC,
             p.is_favorite DESC,
             COALESCE(NULLIF(p.nickname, ''), p.name) ASC
    """


async def _rankings_from_db(db, scope: str = "general") -> list:
    rows = await db.execute(_rankings_sql(scope))
    participants = await rows.fetchall()
    rankings = []
    previous_points = None
    current_rank = 0
    for index, p in enumerate(participants, start=1):
        if previous_points is None or p["total_points"] != previous_points:
            current_rank = index
            previous_points = p["total_points"]
        rankings.append({
            "full_name": p["name"],
            "rank": current_rank,
            "id": p["id"],
            "name": p["nickname"] or p["name"],
            "nickname": p["nickname"],
            "email": p["email"],
            "avatar_path": p["avatar_path"],
            "department": p["department"],
            "total_points": p["total_points"],
            "matches_scored": p["matches_scored"],
        })
    return rankings


async def get_rankings(db=None, scope: str = "general") -> list:
    """Return ranked list of participants for the given scope."""
    if scope not in _SCOPE_POINTS:
        scope = "general"
    if db is not None:
        return await _rankings_from_db(db, scope)
    async with get_db() as db:
        return await _rankings_from_db(db, scope)


async def get_remontada(db) -> list:
    """Progression de rang entre la fin des groupes et le général actuel.

    Baseline déterministe: points de groupes + pré-tournoi + bonus
    pré-tournoi. delta > 0 = places gagnées depuis la fin des groupes.
    """
    baseline = await _rankings_from_db(db, "groups_baseline")
    current = await _rankings_from_db(db, "general")
    baseline_rank = {r["id"]: r["rank"] for r in baseline}
    rows = []
    for r in current:
        old_rank = baseline_rank.get(r["id"], r["rank"])
        rows.append({**r, "baseline_rank": old_rank, "delta": old_rank - r["rank"]})
    rows.sort(key=lambda r: (-r["delta"], r["rank"]))
    previous_delta = None
    current_rank = 0
    for index, r in enumerate(rows, start=1):
        if previous_delta is None or r["delta"] != previous_delta:
            current_rank = index
            previous_delta = r["delta"]
        r["remontada_rank"] = current_rank
    return rows


async def get_department_rankings(db) -> list:
    """Classement des départements à la moyenne de points par inscrit."""
    rankings = await _rankings_from_db(db, "general")
    departments = {}
    for r in rankings:
        dept = (r.get("department") or "").strip() or "Sans département"
        bucket = departments.setdefault(dept, {"department": dept, "members": 0, "total": 0})
        bucket["members"] += 1
        bucket["total"] += r["total_points"]
    rows = []
    for bucket in departments.values():
        bucket["average"] = round(bucket["total"] / bucket["members"], 1) if bucket["members"] else 0.0
        rows.append(bucket)
    rows.sort(key=lambda r: (-r["average"], -r["members"], r["department"]))
    previous_avg = None
    current_rank = 0
    for index, r in enumerate(rows, start=1):
        if previous_avg is None or r["average"] != previous_avg:
            current_rank = index
            previous_avg = r["average"]
        r["rank"] = current_rank
    return rows


def _local_today() -> str:
    return now_utc().astimezone(DISPLAY_TZ).strftime("%Y-%m-%d")


async def record_ranking_snapshot(db):
    """Photographie du classement général pour la date locale du jour.

    Appelée après chaque recalcul: les flèches d'évolution comparent le
    classement courant au dernier snapshot d'un jour précédent.
    """
    today = _local_today()
    rankings = await _rankings_from_db(db, "general")
    for r in rankings:
        await db.execute(
            """INSERT INTO ranking_snapshots (snapshot_date, participant_id, rank, total_points)
               VALUES (?,?,?,?)
               ON CONFLICT(snapshot_date, participant_id)
               DO UPDATE SET rank=excluded.rank, total_points=excluded.total_points""",
            (today, r["id"], r["rank"], r["total_points"]),
        )


async def get_rank_evolution(db) -> dict:
    """participant_id → delta de rang du dernier mouvement réel du classement.

    Positif = places gagnées. Tant qu'aucun résultat n'a été encodé
    aujourd'hui (nuit, matinée, jours sans match), la référence reste
    l'avant-dernière journée photographiée : l'évolution de la veille ne
    s'efface pas à minuit, elle est remplacée par celle du jour en cours au
    premier encodage. Vide tant qu'aucun snapshot antérieur n'existe.
    """
    latest_row = await db.execute("SELECT MAX(snapshot_date) AS d FROM ranking_snapshots")
    latest = (await latest_row.fetchone())["d"]
    if not latest:
        return {}
    today = _local_today()
    pivot = today if latest == today else latest
    row = await db.execute(
        "SELECT MAX(snapshot_date) AS d FROM ranking_snapshots WHERE snapshot_date < ?",
        (pivot,),
    )
    last = (await row.fetchone())["d"]
    if not last:
        return {}
    rows = await db.execute(
        "SELECT participant_id, rank FROM ranking_snapshots WHERE snapshot_date=?",
        (last,),
    )
    old = {r["participant_id"]: r["rank"] for r in await rows.fetchall()}
    current = await _rankings_from_db(db, "general")
    return {r["id"]: old[r["id"]] - r["rank"] for r in current if r["id"] in old}


def answers_match(answer_type: str, given: str, correct: str) -> bool:
    """Tolerant comparison of a participant answer against the correct one."""
    given = (given or "").strip()
    correct = (correct or "").strip()
    if not given or not correct:
        return False
    if answer_type == "number":
        try:
            return float(given.replace(",", ".")) == float(correct.replace(",", "."))
        except ValueError:
            return given.casefold() == correct.casefold()
    if answer_type == "text":
        return given.casefold() == correct.casefold()
    return given == correct


async def calculate_bonus_scores(question_id: int):
    """Calculate scores for a bonus question after correct answer is set."""
    async with get_db() as db:
        row = await db.execute(
            "SELECT * FROM bonus_questions WHERE id = ?", (question_id,)
        )
        question = await row.fetchone()
        if not question:
            return

        await db.execute(
            "DELETE FROM scores WHERE bonus_question_id = ?", (question_id,)
        )

        if question["correct_answer"] is not None:
            rows = await db.execute(
                "SELECT * FROM bonus_answers WHERE question_id = ?", (question_id,)
            )
            answers = await rows.fetchall()
            for ans in answers:
                correct = answers_match(
                    question["answer_type"], ans["answer"], question["correct_answer"]
                )
                points = question["points_value"] if correct else 0
                await db.execute(
                    """INSERT INTO scores (participant_id, bonus_question_id, points)
                       VALUES (?, ?, ?)""",
                    (ans["participant_id"], question_id, points),
                )

        await record_ranking_snapshot(db)
        await db.commit()


# Points awarded for a near miss on the total-goals question (exact = points_value).
TOTAL_GOALS_NEAR_POINTS = 4
TOTAL_GOALS_NEAR_MARGIN = 3
FINALIST_POINTS = 7


def calculate_finalists_points(prediction: dict, correct_answers: dict) -> int:
    """Award points for the two finalists: champion pick + other finalist pick."""
    predicted_finalists = {
        (prediction.get("winner") or "").strip(),
        (prediction.get("finalist") or "").strip(),
    }
    correct_finalists = {
        (correct_answers.get("winner") or "").strip(),
        (correct_answers.get("finalist") or "").strip(),
    }
    predicted_finalists.discard("")
    correct_finalists.discard("")
    return len(predicted_finalists & correct_finalists) * FINALIST_POINTS


def calculate_pre_tournament_points(
    question: dict,
    prediction_value,
    prediction: dict | None = None,
    correct_answers: dict | None = None,
) -> int:
    """Points for one pre-tournament question given its correct answer.

    `question` needs: key, points_value, correct_answer.
    total_goals: full points if exact, TOTAL_GOALS_NEAR_POINTS if within ±3.
    """
    if question["key"] == "finalist" and prediction is not None and correct_answers is not None:
        return calculate_finalists_points(prediction, correct_answers)
    correct = question.get("correct_answer")
    if correct is None or str(correct).strip() == "":
        return 0
    if prediction_value is None or str(prediction_value).strip() == "":
        return 0
    points_value = question.get("points_value") or 0
    if question["key"] == "revelation":
        # Several outsiders can win on a tie (same furthest stage reached):
        # the pick scores if it is among the winning set.
        winners = parse_revelation_winners(correct)
        return points_value if str(prediction_value).strip() in winners else 0
    if question["key"] == "total_goals":
        try:
            predicted = int(str(prediction_value).strip())
            actual = int(str(correct).strip())
        except ValueError:
            return 0
        if predicted == actual:
            return points_value
        if abs(predicted - actual) <= TOTAL_GOALS_NEAR_MARGIN:
            return TOTAL_GOALS_NEAR_POINTS
        return 0
    return points_value if str(prediction_value).strip() == str(correct).strip() else 0


async def recalculate_pre_tournament_scores():
    """Recompute all pre-tournament scores from the stored correct answers."""
    async with get_db() as db:
        q_rows = await db.execute(
            """SELECT key, points_value, correct_answer
               FROM pre_tournament_questions WHERE is_enabled=1"""
        )
        questions = [dict(r) for r in await q_rows.fetchall()]
        correct_answers = {q["key"]: q.get("correct_answer") for q in questions}

        # Toute réponse enregistrée compte — pas de piège du brouillon oublié.
        p_rows = await db.execute(
            "SELECT * FROM pre_tournament_predictions"
        )
        predictions = [dict(r) for r in await p_rows.fetchall()]

        await db.execute("DELETE FROM pre_tournament_scores")

        for question in questions:
            if not (question["correct_answer"] or "").strip():
                continue
            for pred in predictions:
                points = calculate_pre_tournament_points(
                    question,
                    pred.get(question["key"]),
                    prediction=pred,
                    correct_answers=correct_answers,
                )
                await db.execute(
                    """INSERT INTO pre_tournament_scores (participant_id, question_key, points)
                       VALUES (?, ?, ?)""",
                    (pred["participant_id"], question["key"], points),
                )

        await record_ranking_snapshot(db)
        await db.commit()
