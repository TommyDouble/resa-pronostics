"""Score calculation logic per spec."""
import json
from decimal import Decimal, InvalidOperation

from app.database import get_db
from app.timeutils import sporting_day


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

        await sync_finalized_evolution_history(db, from_day=sporting_day(match_dict))
        from app.trophies import refresh_trophy_awards
        await refresh_trophy_awards(db)
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
                      WHERE s.participant_id = p.id AND bq.phase IN ('pre_tournament', 'group')), 0)
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
        bucket = departments.setdefault(dept, {
            "department": dept,
            "members": 0,
            "total": 0,
            "participants": [],
        })
        bucket["members"] += 1
        bucket["total"] += r["total_points"]
        # ``rankings`` est déjà trié comme le classement général : points
        # décroissants, favori admin, puis nom d'affichage.
        bucket["participants"].append({
            "id": r["id"],
            "name": r["name"],
            "total_points": r["total_points"],
        })
    rows = []
    for bucket in departments.values():
        bucket["average"] = round(bucket["total"] / bucket["members"], 1) if bucket["members"] else 0.0
        bucket["is_provisional"] = bucket["department"] == "Sans département"
        rows.append(bucket)
    rows.sort(key=lambda r: (
        r["department"] == "Sans département",
        r["is_provisional"],
        -r["average"],
        -r["members"],
        r["department"],
    ))
    previous_avg = None
    current_rank = 0
    official_index = 0
    for r in rows:
        if r["is_provisional"]:
            r["rank"] = None
            continue
        official_index += 1
        if previous_avg is None or r["average"] != previous_avg:
            current_rank = official_index
            previous_avg = r["average"]
        r["rank"] = current_rank
    return rows


def _ranks_from_points(points_by_id: dict[int, int]) -> dict[int, int]:
    """Rangs compétition : ex æquo au même rang, rangs suivants sautés."""
    values = list(points_by_id.values())
    return {
        pid: 1 + sum(1 for value in values if value > points)
        for pid, points in points_by_id.items()
    }


async def get_sporting_day_states(db) -> dict[str, dict]:
    """Matchs groupés selon la fenêtre locale 9 h–8 h 59."""
    rows = await db.execute(
        "SELECT id, match_date, kickoff_time, result FROM matches ORDER BY match_date, kickoff_time"
    )
    states = {}
    for row in await rows.fetchall():
        match = dict(row)
        day = sporting_day(match)
        bucket = states.setdefault(day, {"day": day, "match_ids": [], "match_count": 0,
                                         "encoded_count": 0, "finalized": False})
        bucket["match_ids"].append(match["id"])
        bucket["match_count"] += 1
        if match["result"] is not None:
            bucket["encoded_count"] += 1
    for bucket in states.values():
        bucket["finalized"] = (
            bucket["match_count"] > 0
            and bucket["encoded_count"] == bucket["match_count"]
        )
    return states


async def _match_points_by_sporting_day(db) -> dict[str, dict[int, int]]:
    rows = await db.execute(
        """SELECT s.participant_id, s.points, m.match_date, m.kickoff_time
           FROM scores s JOIN matches m ON m.id=s.match_id
           WHERE s.match_id IS NOT NULL"""
    )
    result = {}
    for row in await rows.fetchall():
        item = dict(row)
        day = sporting_day(item)
        bucket = result.setdefault(day, {})
        bucket[item["participant_id"]] = bucket.get(item["participant_id"], 0) + item["points"]
    return result


def _evolution_payload(current: list, day_points: dict[int, int], day: str,
                       status: str, match_count: int, encoded_count: int) -> dict:
    after_points = {r["id"]: r["total_points"] for r in current}
    before_points = {
        pid: points - day_points.get(pid, 0) for pid, points in after_points.items()
    }
    before_ranks = _ranks_from_points(before_points)
    after_ranks = {r["id"]: r["rank"] for r in current}
    deltas = {pid: before_ranks[pid] - after_ranks[pid] for pid in after_points}
    return {
        "deltas": deltas,
        "day": day,
        "status": status,
        "match_count": match_count,
        "encoded_count": encoded_count,
        "ranks_before": before_ranks,
        "ranks_after": after_ranks,
        "points_before": before_points,
        "points_after": after_points,
        "day_points": {pid: day_points.get(pid, 0) for pid in after_points},
    }


async def get_rank_evolution(db) -> dict:
    """Évolution live de la dernière journée sportive ayant un résultat.

    Tant que la journée suivante n'a aucun résultat, la dernière évolution
    finalisée reste visible. Dès le premier encodage, la nouvelle journée prend
    le relais et ses deltas deviennent cumulatifs.
    """
    empty = {
        "deltas": {}, "day": None, "status": None,
        "match_count": 0, "encoded_count": 0,
        "ranks_before": {}, "ranks_after": {}, "points_before": {},
        "points_after": {}, "day_points": {},
    }
    states = await get_sporting_day_states(db)
    candidates = [day for day, state in states.items() if state["encoded_count"] > 0]
    if not candidates:
        return empty
    day = max(candidates)
    state = states[day]
    points_by_day = await _match_points_by_sporting_day(db)
    current = await _rankings_from_db(db, "general")
    return _evolution_payload(
        current,
        points_by_day.get(day, {}),
        day,
        "finalized" if state["finalized"] else "in_progress",
        state["match_count"],
        state["encoded_count"],
    )


async def sync_finalized_evolution_history(db, from_day: str | None = None) -> None:
    """Backfill/recalcul des journées complètes, idempotent et correction-safe."""
    states = await get_sporting_day_states(db)
    finalized_days = sorted(
        day for day, state in states.items()
        if state["finalized"] and (from_day is None or day >= from_day)
    )
    if not finalized_days:
        return
    current = await _rankings_from_db(db, "general")
    current_points = {r["id"]: r["total_points"] for r in current}
    all_day_points = await _match_points_by_sporting_day(db)
    all_days = sorted(all_day_points)

    for day in finalized_days:
        future_points = {pid: 0 for pid in current_points}
        for future_day in all_days:
            if future_day <= day:
                continue
            for pid, points in all_day_points[future_day].items():
                if pid in future_points:
                    future_points[pid] += points
        after_points = {
            pid: points - future_points.get(pid, 0) for pid, points in current_points.items()
        }
        day_points = all_day_points.get(day, {})
        before_points = {
            pid: points - day_points.get(pid, 0) for pid, points in after_points.items()
        }
        before_ranks = _ranks_from_points(before_points)
        after_ranks = _ranks_from_points(after_points)
        deltas = {pid: before_ranks[pid] - after_ranks[pid] for pid in current_points}
        best_delta = max(deltas.values(), default=0)
        climber_ids = {pid for pid, delta in deltas.items() if delta == best_delta} if best_delta >= 2 else set()

        existing = await db.execute(
            "SELECT MIN(finalized_at) AS finalized_at FROM sporting_day_rank_evolutions WHERE sporting_day=?",
            (day,),
        )
        finalized_at = (await existing.fetchone())["finalized_at"]
        await db.execute("DELETE FROM sporting_day_rank_evolutions WHERE sporting_day=?", (day,))
        for pid in current_points:
            await db.execute(
                """INSERT INTO sporting_day_rank_evolutions
                   (sporting_day, participant_id, points_before, day_points, points_after,
                    rank_before, rank_after, delta, is_climber, finalized_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,COALESCE(?, datetime('now')),datetime('now'))""",
                (
                    day, pid, before_points[pid], day_points.get(pid, 0), after_points[pid],
                    before_ranks[pid], after_ranks[pid], deltas[pid],
                    1 if pid in climber_ids else 0, finalized_at,
                ),
            )


async def get_latest_finalized_climbers(db) -> dict:
    row = await db.execute(
        "SELECT MAX(sporting_day) AS day FROM sporting_day_rank_evolutions"
    )
    day = (await row.fetchone())["day"]
    if not day:
        return {"day": None, "delta": 0, "climbers": []}
    rows = await db.execute(
        """SELECT e.*, p.name, p.nickname
           FROM sporting_day_rank_evolutions e
           JOIN participants p ON p.id=e.participant_id
           WHERE e.sporting_day=? AND e.is_climber=1
           ORDER BY COALESCE(NULLIF(p.nickname, ''), p.name)""",
        (day,),
    )
    climbers = [dict(r) for r in await rows.fetchall()]
    return {
        "day": day,
        "delta": max((r["delta"] for r in climbers), default=0),
        "climbers": climbers,
    }


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


def parse_bonus_number(value) -> Decimal | None:
    """Parse a bonus numeric answer with comma/dot tolerance."""
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _clean_points(value: Decimal):
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def normalize_closest_config(points_value: int, raw_config=None) -> dict:
    """Normalized config for numeric closest-answer bonus questions."""
    default_rank_points = [int(points_value), max(int(points_value) - 2, 0), max(int(points_value) - 4, 0)]
    config = {}
    if raw_config:
        try:
            parsed = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
            if isinstance(parsed, dict):
                config = parsed
        except (TypeError, ValueError):
            config = {}

    award_mode = config.get("award_mode")
    if award_mode not in {"podium_custom", "winner_takes_all"}:
        award_mode = "podium_custom"

    tie_policy = config.get("tie_policy")
    if tie_policy not in {"full_skip", "full_dense", "share_occupied"}:
        tie_policy = "full_skip"

    raw_points = config.get("rank_points")
    rank_points = []
    if isinstance(raw_points, list):
        for value in raw_points[:3]:
            try:
                rank_points.append(max(int(value), 0))
            except (TypeError, ValueError):
                rank_points.append(0)
    while len(rank_points) < 3:
        rank_points.append(default_rank_points[len(rank_points)])

    if award_mode == "winner_takes_all":
        rank_points = [rank_points[0], 0, 0]

    preset_key = config.get("preset_key")
    if preset_key not in {"fun_balanced", "winner_takes_all", "top2", "custom"}:
        if award_mode == "winner_takes_all" and rank_points[0] == 6:
            preset_key = "winner_takes_all"
        elif award_mode == "podium_custom" and tie_policy == "full_skip" and rank_points == [6, 4, 2]:
            preset_key = "fun_balanced"
        elif award_mode == "podium_custom" and tie_policy == "full_skip" and rank_points == [6, 3, 0]:
            preset_key = "top2"
        else:
            preset_key = "custom"

    return {
        "preset_key": preset_key,
        "award_mode": award_mode,
        "tie_policy": tie_policy,
        "rank_points": rank_points,
    }


def serialize_closest_config(
    points_value: int,
    award_mode: str,
    tie_policy: str,
    rank_points: list[int],
    preset_key: str = "custom",
) -> str:
    config = normalize_closest_config(
        points_value,
        {
            "preset_key": preset_key,
            "award_mode": award_mode,
            "tie_policy": tie_policy,
            "rank_points": rank_points,
        },
    )
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def _format_points(value) -> str:
    points = int(value)
    return f"{points} pt" if points == 1 else f"{points} pts"


def _rank_points_label(rank_points: list[int]) -> str:
    positive_points = [points for points in rank_points if points > 0]
    if not positive_points:
        return "0 pt"
    if len(positive_points) == 1:
        return _format_points(positive_points[0])
    return f"{' / '.join(str(points) for points in positive_points)} pts"


def bonus_points_summary_label(
    points_value: int,
    answer_type: str,
    scoring_mode: str = "exact",
    scoring_config=None,
) -> str:
    """Short public label for a bonus question points rule."""
    if scoring_mode == "closest_podium" or answer_type == "number":
        config = normalize_closest_config(points_value, scoring_config)
        if config["award_mode"] == "winner_takes_all":
            return f"{_format_points(config['rank_points'][0])} au plus proche"
        return _rank_points_label(config["rank_points"])
    return f"{_format_points(points_value)} si correct"


def default_bonus_points_explanation(
    points_value: int,
    answer_type: str,
    scoring_mode: str = "exact",
    scoring_config=None,
) -> str:
    """Default public explanation for a bonus question points rule."""
    if scoring_mode != "closest_podium" and answer_type != "number":
        return f"Bonne réponse : {_format_points(points_value)}."

    config = normalize_closest_config(points_value, scoring_config)
    rank_points = config["rank_points"]
    if config["award_mode"] == "winner_takes_all":
        return (
            f"Le ou les plus proches remportent {_format_points(rank_points[0])}. "
            "Les autres ne marquent pas."
        )

    tiers = []
    for index, points in enumerate(rank_points, start=1):
        if points <= 0:
            continue
        label = "1er" if index == 1 else f"{index}e"
        tiers.append(f"{label} {_format_points(points)}")
    tier_text = ", ".join(tiers) if tiers else "aucun point"
    tie_text = {
        "full_skip": "les ex aequo reçoivent le plein palier et les rangs suivants sont sautés.",
        "full_dense": "les ex aequo reçoivent le plein palier sans sauter le rang suivant.",
        "share_occupied": "les ex aequo se partagent les points des places occupées.",
    }.get(config["tie_policy"], "les ex aequo reçoivent le plein palier.")
    return f"Réponses classées par écart : {tier_text}. Ex aequo : {tie_text}"


def bonus_points_explanation(
    points_value: int,
    answer_type: str,
    scoring_mode: str = "exact",
    scoring_config=None,
    custom_explanation: str | None = None,
) -> str:
    """Effective public explanation, using the admin text when present."""
    cleaned = (custom_explanation or "").strip()
    if cleaned:
        return cleaned
    return default_bonus_points_explanation(
        points_value,
        answer_type,
        scoring_mode,
        scoring_config,
    )


def _closest_group_points(rank: int, tie_size: int, rank_points: list[int], tie_policy: str):
    if tie_policy == "share_occupied":
        total = Decimal(0)
        for place in range(rank, rank + tie_size):
            if 1 <= place <= len(rank_points):
                total += Decimal(rank_points[place - 1])
        return _clean_points(total / Decimal(tie_size))
    if 1 <= rank <= len(rank_points):
        return rank_points[rank - 1]
    return 0


def closest_bonus_standings(points_value: int, correct_answer, answers, scoring_config=None) -> dict:
    """Rank numeric bonus answers by distance from the official answer."""
    actual = parse_bonus_number(correct_answer)
    config = normalize_closest_config(points_value, scoring_config)
    standings = {
        "actual": actual,
        "groups": [],
        "invalid": [],
        "config": config,
    }
    if actual is None:
        return standings

    by_distance = {}
    for ans in answers:
        predicted = parse_bonus_number(_row_get(ans, "answer"))
        if predicted is None:
            standings["invalid"].append(ans)
            continue
        distance = abs(predicted - actual)
        by_distance.setdefault(distance, []).append(ans)

    better_count = 0
    for group_index, distance in enumerate(sorted(by_distance), start=1):
        participants = by_distance[distance]
        rank = group_index if config["tie_policy"] == "full_dense" else better_count + 1
        points = _closest_group_points(
            rank,
            len(participants),
            config["rank_points"],
            config["tie_policy"],
        )
        standings["groups"].append({
            "rank": rank,
            "distance": distance,
            "points": points,
            "participants": participants,
        })
        better_count += len(participants)
    return standings


def closest_podium_bonus_points(points_value: int, correct_answer, answers, scoring_config=None) -> dict[int, int]:
    """Points for numeric closest-answer bonus questions.

    Ties receive the full points for their competition rank. If two people tie
    for first, the next distance is rank 3 and receives the third-place tier.
    """
    scores = {ans["participant_id"]: 0 for ans in answers}
    standings = closest_bonus_standings(points_value, correct_answer, answers, scoring_config)
    for group in standings["groups"]:
        for ans in group["participants"]:
            scores[_row_get(ans, "participant_id")] = group["points"]
    return scores


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
            if question["scoring_mode"] == "closest_podium":
                points_by_participant = closest_podium_bonus_points(
                    question["points_value"],
                    question["correct_answer"],
                    answers,
                    question["scoring_config"],
                )
            else:
                points_by_participant = {}
            for ans in answers:
                if question["scoring_mode"] == "closest_podium":
                    points = points_by_participant.get(ans["participant_id"], 0)
                else:
                    correct = answers_match(
                        question["answer_type"], ans["answer"], question["correct_answer"]
                    )
                    points = question["points_value"] if correct else 0
                await db.execute(
                    """INSERT INTO scores (participant_id, bonus_question_id, points)
                       VALUES (?, ?, ?)""",
                    (ans["participant_id"], question_id, points),
                )

        await sync_finalized_evolution_history(db)
        from app.trophies import refresh_trophy_awards
        await refresh_trophy_awards(db)
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

        await sync_finalized_evolution_history(db)
        from app.trophies import refresh_trophy_awards
        await refresh_trophy_awards(db)
        await db.commit()
