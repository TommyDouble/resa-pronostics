"""Score calculation logic per spec."""
import json
from decimal import Decimal, InvalidOperation

from app.database import get_db
from app.timeutils import sporting_day, sporting_day_for_timestamp


def parse_team_set(value) -> set:
    """Parse a stored answer holding several items (teams) into a set.

    Accepts a JSON list (canonical format for multi-choice answers) or a single
    string (legacy / single value). Returns a set of non-empty trimmed strings.
    """
    if not value:
        return set()
    if isinstance(value, (list, tuple, set)):
        return {str(t).strip() for t in value if str(t).strip()}
    raw = str(value).strip()
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


def format_team_list(value) -> str:
    """Human-readable rendering of a stored multi-choice answer."""
    teams = parse_team_set(value)
    if not teams:
        return str(value or "")
    return ", ".join(sorted(teams))


def parse_revelation_winners(correct_answer) -> set:
    """Winning outsiders for the révélation question.

    Stored as a JSON list (new format, supports ties → several winning teams).
    Falls back to a single team string for legacy answers. Returns a set of
    non-empty team names.
    """
    return parse_team_set(correct_answer)


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
    """Journées groupées selon la fenêtre locale 9 h–8 h 59.

    Inclut aussi les journées dont l'unique activité est un événement bonus ou
    pré-tournoi (aucun match ce jour-là) : elles sont "finalisées" par
    construction (0 match encodé sur 0), un encodage bonus/pré-tournoi étant
    une action ponctuelle et complète, pas un état "en cours" comme une nuit de
    matchs qui s'encode match par match.
    """
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

    scoring_days = await _scoring_points_by_day(db)
    for day in scoring_days:
        states.setdefault(day, {"day": day, "match_ids": [], "match_count": 0,
                                 "encoded_count": 0, "finalized": False})

    for bucket in states.values():
        bucket["finalized"] = bucket["match_count"] == bucket["encoded_count"]
    return states


async def _scoring_points_by_day(db) -> dict[str, dict[int, int]]:
    """Points attribués par jour, toutes sources confondues.

    Matchs : relus "à la volée" (valeur courante de `scores`, jour du coup
    d'envoi) — jour fixe, donc toujours correct même après une correction
    tardive du résultat (cf. `test_late_correction_replaces_persisted_climber`).

    Bonus/pré-tournoi : n'ont pas de jour fixe (leur seul repère temporel est
    "quand a-t-on corrigé la question", qui change à chaque nouvelle
    correction) — relire leur valeur courante bucketée par date de recalcul
    romprait le delta en cas de correction rétroactive (5 -> 2 pts afficherait
    +2 au lieu de -3). On lit donc à la place les deltas déjà journalisés dans
    `scoring_point_events` (un événement = un vrai changement de valeur, jamais
    réécrit), horodatés au jour de la correction.
    """
    result: dict[str, dict[int, int]] = {}

    def _add(day: str, pid: int, points: int) -> None:
        bucket = result.setdefault(day, {})
        bucket[pid] = bucket.get(pid, 0) + points

    rows = await db.execute(
        """SELECT s.participant_id, s.points, m.match_date, m.kickoff_time
           FROM scores s JOIN matches m ON m.id=s.match_id
           WHERE s.match_id IS NOT NULL"""
    )
    for row in await rows.fetchall():
        item = dict(row)
        _add(sporting_day(item), item["participant_id"], item["points"])

    event_rows = await db.execute(
        "SELECT participant_id, delta, occurred_at FROM scoring_point_events"
    )
    for row in await event_rows.fetchall():
        _add(sporting_day_for_timestamp(row["occurred_at"]), row["participant_id"], row["delta"])

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
    candidates = [
        day for day, state in states.items()
        if state["encoded_count"] > 0 or state["match_count"] == 0
    ]
    if not candidates:
        return empty
    day = max(candidates)
    state = states[day]
    points_by_day = await _scoring_points_by_day(db)
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
    all_day_points = await _scoring_points_by_day(db)
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


def bonus_number_is_integer(value: Decimal) -> bool:
    """Whether a parsed numeric bonus answer is an integer value."""
    return value == value.to_integral_value()


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

    def _optional_int(value):
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    min_value = _optional_int(config.get("min_value"))
    max_value = _optional_int(config.get("max_value"))
    if min_value is not None and max_value is not None and max_value < min_value:
        max_value = min_value
    integer_only = config.get("integer_only")
    if isinstance(integer_only, str):
        integer_only = integer_only.strip().lower() in {"1", "true", "yes", "on"}
    else:
        integer_only = bool(integer_only)

    return {
        "preset_key": preset_key,
        "award_mode": award_mode,
        "tie_policy": tie_policy,
        "rank_points": rank_points,
        "min_value": min_value,
        "max_value": max_value,
        "integer_only": integer_only,
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
    if config.get("min_value") is None:
        config.pop("min_value", None)
    if config.get("max_value") is None:
        config.pop("max_value", None)
    if not config.get("integer_only"):
        config.pop("integer_only", None)
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


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
    if config["integer_only"] and not bonus_number_is_integer(actual):
        standings["invalid_actual"] = True
        return standings

    by_distance = {}
    for ans in answers:
        predicted = parse_bonus_number(_row_get(ans, "answer"))
        if predicted is None:
            standings["invalid"].append(ans)
            continue
        if config["integer_only"] and not bonus_number_is_integer(predicted):
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


def parse_number_multi(value) -> dict:
    """Parse a combined "number + teams" answer.

    Stored as JSON ``{"count": int, "teams": [..]}``. Returns
    ``{"count": int|None, "teams": set}``.
    """
    count = None
    teams = set()
    if not value:
        return {"count": count, "teams": teams}
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        raw_count = parsed.get("count")
        try:
            count = int(raw_count) if raw_count is not None and str(raw_count).strip() != "" else None
        except (TypeError, ValueError):
            count = None
        teams = parse_team_set(parsed.get("teams"))
    return {"count": count, "teams": teams}


def format_number_multi(value, options=None) -> str:
    """Human-readable rendering of a combined number+teams answer."""
    parsed = parse_number_multi(value)
    count = parsed["count"]
    option_order = _config_list(options)
    teams = [team for team in option_order if team in parsed["teams"]]
    teams.extend(sorted(parsed["teams"] - set(teams)))
    parts = []
    if count is not None:
        parts.append(f"{count} au total")
    if teams:
        parts.append(", ".join(teams))
    return " — ".join(parts) if parts else str(value or "")


def _config_list(value) -> list[str]:
    """Return a stable list of non-empty strings from a JSON/list config value."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = [value]
    else:
        parsed = value
    if not isinstance(parsed, (list, tuple, set)):
        parsed = [parsed]
    seen = set()
    items = []
    for item in parsed:
        label = str(item).strip()
        if label and label not in seen:
            seen.add(label)
            items.append(label)
    return items


def normalize_number_multi_config(points_value: int = 0, raw_config=None, options=None) -> dict:
    """Normalized config for combined number + multi-choice questions."""
    config = {}
    if raw_config:
        try:
            parsed = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
            if isinstance(parsed, dict):
                config = parsed
        except (TypeError, ValueError):
            config = {}

    option_items = _config_list(options)
    locked_teams = _config_list(config.get("locked_teams"))
    default_max = len(option_items) if option_items else max(int(points_value or 0), 1)

    def _positive_int(value, default):
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return default

    min_count = _positive_int(config.get("min_count"), max(len(locked_teams), 0))
    max_count = _positive_int(config.get("max_count"), default_max)
    if max_count < min_count:
        max_count = min_count

    part1_points = _positive_int(config.get("part1_points"), 3)
    team_step = max(_positive_int(config.get("team_step"), 1), 1)
    selectable_count = max(max_count - len(locked_teams), 0)
    max_points = _positive_int(
        config.get("max_points"),
        part1_points + team_step * selectable_count,
    )

    return {
        "locked_teams": locked_teams,
        "min_count": min_count,
        "max_count": max_count,
        "part1_points": part1_points,
        "team_step": team_step,
        "max_points": max_points,
    }


def number_multi_bonus_points(points_value: int, correct_answer, answers, scoring_config=None) -> dict[int, int]:
    """Points for the combined "number + qualifying teams" question (Q1).

    Part 1 (the total number): ``part1_points`` (default 3) all-or-nothing on an
    exact match. Part 2 (the teams): ``+team_step`` per correctly selected team,
    ``-team_step`` per wrongly selected team, floored at 0. Total = part1 + part2.
    """
    correct = parse_number_multi(correct_answer)
    config = normalize_number_multi_config(points_value, scoring_config)
    part1_points = config["part1_points"]
    team_step = config["team_step"]
    locked = set(config["locked_teams"])
    correct_teams = correct["teams"] - locked
    scores = {}
    for ans in answers:
        pid = _row_get(ans, "participant_id")
        given = parse_number_multi(_row_get(ans, "answer"))
        p1 = part1_points if (
            given["count"] is not None
            and correct["count"] is not None
            and given["count"] == correct["count"]
        ) else 0
        given_teams = given["teams"] - locked
        good = len(given_teams & correct_teams)
        wrong = len(given_teams - correct_teams)
        p2 = max(team_step * good - team_step * wrong, 0)
        scores[pid] = p1 + p2
    return scores


def multi_select_bonus_points(points_value: int, correct_answer, answers, scoring_config=None) -> dict[int, int]:
    """Points for a multi-choice bonus question (e.g. "which teams qualify?").

    Each error costs ``error_step`` points (default 2), floored at 0. An error is
    a team checked by mistake OR a correct team forgotten — i.e. the size of the
    symmetric difference between the participant's set and the correct set.
    """
    correct_set = parse_team_set(correct_answer)
    step = 2
    if scoring_config:
        try:
            cfg = json.loads(scoring_config) if isinstance(scoring_config, str) else scoring_config
            if isinstance(cfg, dict) and cfg.get("error_step"):
                step = max(int(cfg["error_step"]), 1)
        except (TypeError, ValueError):
            step = 2
    scores = {}
    for ans in answers:
        pid = _row_get(ans, "participant_id")
        given = parse_team_set(_row_get(ans, "answer"))
        errors = len(given ^ correct_set)
        scores[pid] = max(int(points_value) - step * errors, 0)
    return scores


async def _log_scoring_point_events(
    db, source: str, source_key: str, old_points: dict[int, int], new_points: dict[int, int]
) -> None:
    """Journalise le delta réel (pas la valeur absolue) pour chaque participant
    dont les points bonus/pré-tournoi ont changé. Aucune ligne si rien ne change
    (resave, édition sans rapport) — voir la note "delta vs valeur absolue" du
    plan de correction : une simple valeur courante horodatée ne peut pas
    représenter correctement une correction rétroactive (ex. 5 -> 2 pts)."""
    changed = set(old_points) | set(new_points)
    events = []
    for pid in changed:
        delta = new_points.get(pid, 0) - old_points.get(pid, 0)
        if delta != 0:
            events.append((source, source_key, pid, delta))
    if events:
        await db.executemany(
            """INSERT INTO scoring_point_events (source, source_key, participant_id, delta)
               VALUES (?, ?, ?, ?)""",
            events,
        )


async def backfill_scoring_point_events(db) -> None:
    """Backfill idempotent : reconstruit un événement initial pour chaque
    question bonus/pré-tournoi déjà notée AVANT l'existence de cette table
    (déploiement de la journalisation des événements de scoring).

    Sans ce backfill, les points bonus/pré-tournoi déjà encodés en production
    resteraient invisibles pour `_scoring_points_by_day` (qui ne lit QUE
    `scoring_point_events` pour ces deux sources) : flèches de `/classement`,
    Reveal personnel, trophée "Le Grimpeur" et carrousel de badges ne
    refléteraient aucun mouvement causé par ces points déjà en base.

    Idempotence : un (participant, question) n'est JAMAIS rebackfillé s'il a
    déjà au moins un événement journalisé pour CE participant sur CETTE
    question — que ce soit un backfill précédent (rejouer cette fonction ne
    crée donc aucun doublon) ou un vrai recalcul survenu depuis le déploiement
    (évite le double-comptage : ce recalcul a déjà journalisé son propre delta
    correct, le backfill ne doit pas en rajouter un second basé sur la valeur
    absolue). Le suivi est scopé par (source, clé, PARTICIPANT) et non par
    (source, clé) seule : si un seul participant d'une question a déjà un
    événement (ex. un recalcul en direct qui ne l'a touché que lui), les
    AUTRES participants de cette même question, encore legacy, doivent quand
    même être backfillés — un garde-fou par question seule les ferait sauter
    silencieusement, pour toujours (migration one-shot, jamais rejouée).

    Limite assumée (documentée dans le plan de correction) : si une question a
    été corrigée plusieurs fois AVANT ce backfill, seule sa valeur FINALE
    actuellement en base est reconstructible (`delta = points`, `occurred_at =
    calculated_at`) — les corrections intermédiaires déjà écrasées ne sont
    récupérables nulle part, faute d'avoir jamais été journalisées.
    """
    tracked_rows = await db.execute(
        "SELECT DISTINCT source, source_key, participant_id FROM scoring_point_events"
    )
    tracked = {
        (r["source"], r["source_key"], r["participant_id"])
        for r in await tracked_rows.fetchall()
    }

    events = []

    bonus_rows = await db.execute(
        """SELECT participant_id, bonus_question_id, points, calculated_at
           FROM scores WHERE bonus_question_id IS NOT NULL"""
    )
    for r in await bonus_rows.fetchall():
        key = str(r["bonus_question_id"])
        if ("bonus", key, r["participant_id"]) in tracked or not r["points"]:
            continue
        events.append(("bonus", key, r["participant_id"], r["points"], r["calculated_at"]))

    pt_rows = await db.execute(
        "SELECT participant_id, question_key, points, calculated_at FROM pre_tournament_scores"
    )
    for r in await pt_rows.fetchall():
        key = r["question_key"]
        if ("pre_tournament", key, r["participant_id"]) in tracked or not r["points"]:
            continue
        events.append(("pre_tournament", key, r["participant_id"], r["points"], r["calculated_at"]))

    if events:
        await db.executemany(
            """INSERT INTO scoring_point_events
               (source, source_key, participant_id, delta, occurred_at)
               VALUES (?, ?, ?, ?, ?)""",
            events,
        )


async def calculate_bonus_scores(question_id: int):
    """Calculate scores for a bonus question after correct answer is set."""
    async with get_db() as db:
        row = await db.execute(
            "SELECT * FROM bonus_questions WHERE id = ?", (question_id,)
        )
        question = await row.fetchone()
        if not question:
            return

        old_rows = await db.execute(
            "SELECT participant_id, points FROM scores WHERE bonus_question_id = ?",
            (question_id,),
        )
        old_points = {r["participant_id"]: r["points"] for r in await old_rows.fetchall()}

        await db.execute(
            "DELETE FROM scores WHERE bonus_question_id = ?", (question_id,)
        )

        new_points: dict[int, int] = {}
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
            elif question["scoring_mode"] == "multi_select":
                points_by_participant = multi_select_bonus_points(
                    question["points_value"],
                    question["correct_answer"],
                    answers,
                    question["scoring_config"],
                )
            elif question["scoring_mode"] == "number_multi":
                points_by_participant = number_multi_bonus_points(
                    question["points_value"],
                    question["correct_answer"],
                    answers,
                    question["scoring_config"],
                )
            else:
                points_by_participant = {}
            for ans in answers:
                if question["scoring_mode"] in ("closest_podium", "multi_select", "number_multi"):
                    points = points_by_participant.get(ans["participant_id"], 0)
                else:
                    correct = answers_match(
                        question["answer_type"], ans["answer"], question["correct_answer"]
                    )
                    points = question["points_value"] if correct else 0
                new_points[ans["participant_id"]] = points
                await db.execute(
                    """INSERT INTO scores (participant_id, bonus_question_id, points)
                       VALUES (?, ?, ?)""",
                    (ans["participant_id"], question_id, points),
                )

        await _log_scoring_point_events(db, "bonus", str(question_id), old_points, new_points)

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

        old_rows = await db.execute(
            "SELECT participant_id, question_key, points FROM pre_tournament_scores"
        )
        old_points: dict[str, dict[int, int]] = {}
        for r in await old_rows.fetchall():
            old_points.setdefault(r["question_key"], {})[r["participant_id"]] = r["points"]

        await db.execute("DELETE FROM pre_tournament_scores")

        new_points: dict[str, dict[int, int]] = {}
        for question in questions:
            if not (question["correct_answer"] or "").strip():
                continue
            key_points: dict[int, int] = {}
            for pred in predictions:
                points = calculate_pre_tournament_points(
                    question,
                    pred.get(question["key"]),
                    prediction=pred,
                    correct_answers=correct_answers,
                )
                key_points[pred["participant_id"]] = points
                await db.execute(
                    """INSERT INTO pre_tournament_scores (participant_id, question_key, points)
                       VALUES (?, ?, ?)""",
                    (pred["participant_id"], question["key"], points),
                )
            new_points[question["key"]] = key_points

        for key in set(old_points) | set(new_points):
            await _log_scoring_point_events(
                db, "pre_tournament", key,
                old_points.get(key, {}), new_points.get(key, {}),
            )

        await sync_finalized_evolution_history(db)
        from app.trophies import refresh_trophy_awards
        await refresh_trophy_awards(db)
        await db.commit()
