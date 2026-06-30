"""Participant-facing HTML page routes."""
from collections import defaultdict
from datetime import date, timedelta
import io
import json
import logging
import os
import unicodedata
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, File, HTTPException, Request, Form, UploadFile
from PIL import Image, ImageOps
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import (
    PARTICIPANT_COOKIE,
    clear_participant_cookie,
    get_participant_by_token,
    hash_password,
    require_participant,
    set_participant_cookie,
    verify_password,
)
from app.config import settings
from app.constants import DEPARTMENTS, MIN_PASSWORD_LENGTH
from app.database import get_db
from app.flags import team_flag
from app.knockout import is_placeholder_team, match_predictions_open, parse_source_label
from app.mail import send_invitation
from app.nameutils import build_full_name
from app.players import (
    OUTSIDERS,
    TEAMS_48,
    get_scorer_options,
    is_valid_scorer,
    normalize_scorer,
)
from app.pre_tournament import (
    get_pre_tournament_deadline,
    get_pre_tournament_question_map,
    get_pre_tournament_questions,
    pt_filled_keys,
)
from app.prizes import get_prize_info
from app.result_display import qualified_team_name, result_full_label
from app.scoring import (
    actual_match_winner,
    format_number_multi,
    format_team_list,
    get_department_rankings,
    get_latest_finalized_climbers,
    get_rank_evolution,
    get_rankings,
    get_remontada,
    is_match_prediction_correct,
    is_match_score_exact,
    parse_bonus_number,
    parse_number_multi,
    parse_team_set,
    predicted_match_winner,
)
from app.templating import create_templates
from app.timeutils import (
    DISPLAY_TZ,
    current_sporting_day,
    format_match_local_date,
    format_sporting_day_fr,
    is_match_locked,
    local_today,
    match_kickoff_utc,
    match_live_state,
    minutes_until_match,
    now_utc,
    now_utc_iso,
    sporting_day,
    sporting_day_bounds,
)

# Whitelist stricte des templates de story (registre central app.news) :
# empêche tout include de chemin arbitraire depuis la BDD.
from app.news import STORY_TEMPLATES
from app.trophies import build_cabinet, latest_ephemeral_badges, all_ephemeral_badges

router = APIRouter()
templates = create_templates()
logger = logging.getLogger(__name__)

PHASE_LABELS = {
    "group": "Phase de groupes",
    "round_of_32": "Seizièmes de finale",
    "round_of_16": "Huitièmes de finale",
    "quarter": "Quarts de finale",
    "semi": "Demi-finales",
    "third_place": "Match pour la 3e place",
    "final": "Finale",
}

GROUP_MATCH_LABELS = {
    1: "Phase de groupes - Journée 1",
    2: "Phase de groupes - Journée 2",
    3: "Phase de groupes - Journée 3",
}

PREDICTION_SECTION_ORDER = [
    "group_match_1",
    "group_match_2",
    "group_match_3",
    "round_of_32",
    "round_of_16",
    "quarter",
    "semi",
    "third_place",
    "final",
]

PREDICTION_SECTION_LABELS = {
    "group_match_1": GROUP_MATCH_LABELS[1],
    "group_match_2": GROUP_MATCH_LABELS[2],
    "group_match_3": GROUP_MATCH_LABELS[3],
    "round_of_32": PHASE_LABELS["round_of_32"],
    "round_of_16": PHASE_LABELS["round_of_16"],
    "quarter": PHASE_LABELS["quarter"],
    "semi": PHASE_LABELS["semi"],
    "third_place": PHASE_LABELS["third_place"],
    "final": PHASE_LABELS["final"],
}

def _now_utc() -> str:
    return now_utc_iso()


async def _pt_status(db, participant_id: int) -> dict:
    """Avancement pré-tournoi: questions remplies / total, complétude."""
    row = await db.execute(
        "SELECT * FROM pre_tournament_predictions WHERE participant_id = ?",
        (participant_id,),
    )
    pt = await row.fetchone()
    questions = await get_pre_tournament_questions(db)
    enabled_keys = [q["key"] for q in questions]
    filled = pt_filled_keys(dict(pt) if pt else None, enabled_keys)
    deadline = await get_pre_tournament_deadline(db)
    return {
        "pt": dict(pt) if pt else {},
        "filled_count": len(filled),
        "question_count": len(enabled_keys),
        "complete": len(filled) == len(enabled_keys) and len(enabled_keys) > 0,
        "open": _now_utc() < deadline,
        "deadline": deadline,
    }


def _display_name(participant: dict) -> str:
    return participant.get("nickname") or participant.get("name") or "Participant"


def _greeting_name(participant: dict) -> str:
    return (
        participant.get("nickname")
        or participant.get("first_name")
        or (participant.get("name") or "Participant").split()[0]
    )


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return "".join(p[0].upper() for p in parts[:2])


def _ordinal_fr(rank) -> str:
    if not isinstance(rank, int):
        return str(rank)
    return "1er" if rank == 1 else f"{rank}e"


def _is_locked(match: dict) -> bool:
    return is_match_locked(match)


def _is_past_day(match: dict) -> bool:
    """Le match s'est joué un jour précédent (fuseau d'affichage)."""
    try:
        return match_kickoff_utc(match).astimezone(DISPLAY_TZ).date() < local_today()
    except Exception:
        return False


def _live_state(match: dict) -> str:
    return match_live_state(match)


def _minutes_until(match: dict) -> int:
    return minutes_until_match(match)


def _enrich_sporting_day_match(match: dict, day: str) -> None:
    """Ajoute les repères lisibles des matchs joués après minuit."""
    calendar_day = format_match_local_date(match)
    match["is_overnight"] = calendar_day != day
    match["calendar_day_label"] = format_sporting_day_fr(calendar_day).split(" ", 1)[0]


def _prediction_short(prediction: str | None) -> str:
    return {"team1": "1", "draw": "X", "team2": "2"}.get(prediction or "", "")


def _prediction_label(match: dict) -> str:
    if match.get("prediction") == "team1":
        return match["team1_name"]
    if match.get("prediction") == "team2":
        return match["team2_name"]
    if match.get("prediction") == "draw":
        return "Nul"
    return "—"


def _prediction_tier(prediction: dict, match: dict) -> str:
    """'exact' | 'correct' | 'wrong' | '' (pas de résultat ou pas de prono)."""
    if match.get("result") is None:
        return ""
    if prediction.get("prediction") is None and prediction.get("exact_score_team1") is None:
        return ""
    if is_match_score_exact(prediction, match):
        return "exact"
    if is_match_prediction_correct(prediction, match):
        return "correct"
    return "wrong"


def _qualifier_label(match: dict) -> str:
    if match.get("qualifier_prediction") == "team1":
        return match["team1_name"]
    if match.get("qualifier_prediction") == "team2":
        return match["team2_name"]
    return ""


def _enrich_knockout_result_labels(row: dict) -> None:
    """Pose les libellés de qualifié pour les écrans de résultat (phase finale).

    - ``qualifier_pred_label`` : l'équipe que le participant voyait passer (nul prono).
    - ``qualifier_real_label`` : l'équipe réellement qualifiée.
    - ``qualifier_match`` : True/False/None selon que le choix correspond.
    """
    row["qualifier_pred_label"] = ""
    row["qualifier_real_label"] = ""
    row["qualifier_match"] = None
    if row.get("phase") == "group":
        return
    pred = row.get("qualifier_prediction")
    if pred in ("team1", "team2"):
        row["qualifier_pred_label"] = row["team1_name"] if pred == "team1" else row["team2_name"]
    if row.get("result") is not None:
        row["qualifier_real_label"] = qualified_team_name(row)
        real = row.get("qualifier_winner") or (
            row.get("result") if row.get("result") in ("team1", "team2") else None
        )
        if pred in ("team1", "team2") and real in ("team1", "team2"):
            row["qualifier_match"] = pred == real


def _zero_points_reason(prediction: dict | None, match: dict) -> str:
    """Raison courte d'un 0 point, à afficher à côté du verdict."""
    if not prediction or prediction.get("exact_score_team1") is None:
        return "Pas de prono"
    if match.get("phase") != "group":
        predicted = predicted_match_winner(prediction, match)
        actual = actual_match_winner(match)
        if predicted and actual and predicted != actual:
            if prediction.get("exact_score_team1") == prediction.get("exact_score_team2"):
                return "Mauvais qualifié"
            return "Mauvaise issue"
    return "Mauvaise issue"


def _resolve_team_label(raw: str, by_number: dict[int, tuple[str, str]]) -> str:
    """« Vainqueur M83 » → « Vainqueur Brésil-France » via les équipes du match source.

    Renvoie le nom réel si ce n'en est pas un placeholder, « À confirmer » si la
    source n'a pas encore d'équipes réelles.
    """
    parsed = parse_source_label(raw)
    if not parsed:
        return raw
    kind, number = parsed
    src = by_number.get(number)
    if src and not is_placeholder_team(src[0]) and not is_placeholder_team(src[1]):
        return f"{kind} {src[0]}-{src[1]}"
    return "À confirmer"


def _enrich_prediction_matches(matches: list[dict]) -> None:
    by_number = {
        m["match_number"]: (m.get("team1_name") or "", m.get("team2_name") or "")
        for m in matches
    }
    grouped = defaultdict(list)
    for match in matches:
        if match["phase"] == "group":
            grouped[match.get("group_name") or ""].append(match)

    for group_matches in grouped.values():
        group_matches.sort(key=lambda item: (
            item["match_date"],
            item["kickoff_time"],
            item["match_number"],
        ))
        for index, match in enumerate(group_matches):
            group_match_no = min((index // 2) + 1, 3)
            match["group_match_no"] = group_match_no
            match["section_key"] = f"group_match_{group_match_no}"
            match["section_label"] = GROUP_MATCH_LABELS[group_match_no]

    for match in matches:
        if match["phase"] != "group":
            match["section_key"] = match["phase"]
            match["section_label"] = PHASE_LABELS.get(match["phase"], match["phase"])
        match["is_locked"] = _is_locked(match)
        match["live_state"] = _live_state(match)
        match["phase_label"] = PHASE_LABELS.get(match["phase"], match["phase"])
        match["has_score_prediction"] = (
            match.get("exact_score_team1") is not None
            and match.get("exact_score_team2") is not None
        )
        match["prediction_short"] = _prediction_short(match.get("prediction"))
        match["prediction_label"] = _prediction_label(match)
        match["qualifier_label"] = _qualifier_label(match)
        match["tier"] = _prediction_tier(match, match)
        match["is_past_day"] = _is_past_day(match)
        t1_placeholder = is_placeholder_team(match.get("team1_name")) if match["phase"] != "group" else False
        t2_placeholder = is_placeholder_team(match.get("team2_name")) if match["phase"] != "group" else False
        match["has_placeholder_team"] = t1_placeholder or t2_placeholder
        match["team1_label"] = _resolve_team_label(match["team1_name"], by_number) if t1_placeholder else match["team1_name"]
        match["team2_label"] = _resolve_team_label(match["team2_name"], by_number) if t2_placeholder else match["team2_name"]


def _prediction_sections(matches: list[dict]) -> list[dict]:
    sections = []
    for key in PREDICTION_SECTION_ORDER:
        section_matches = [m for m in matches if m.get("section_key") == key]
        total = len(section_matches)
        done = sum(1 for m in section_matches if m.get("has_score_prediction"))
        open_count = sum(
            1 for m in section_matches
            if not m.get("is_locked") and not m.get("pronos_closed")
        )
        closed_count = sum(
            1 for m in section_matches
            if not m.get("is_locked") and m.get("pronos_closed")
        )
        sections.append({
            "key": key,
            "label": PREDICTION_SECTION_LABELS[key],
            "short_label": PREDICTION_SECTION_LABELS[key].replace("Phase de groupes - ", ""),
            "total": total,
            "done": done,
            "open_count": open_count,
            "closed_count": closed_count,
        })
    return sections


def _default_prediction_section(sections: list[dict]) -> str:
    for section in sections:
        if section["total"] and section["open_count"]:
            return section["key"]
    for section in sections:
        if section["total"]:
            return section["key"]
    return PREDICTION_SECTION_ORDER[0]


def _section_help(section_key: str) -> str:
    return {
        "group_match_1": "Journée 1 regroupe le premier match de chaque équipe dans son groupe.",
        "group_match_2": "Journée 2 regroupe le deuxième match de chaque équipe dans son groupe.",
        "group_match_3": "Journée 3 regroupe le troisième match de chaque équipe dans son groupe.",
    }.get(
        section_key,
        "En phase finale, les points portent sur l'équipe qualifiée : bon qualifié = 4 pts, +2 si ton score à 90 min est exact.",
    )


async def _record_daily_visit(db, p) -> None:
    """Série de connexions (trophée « Présent ») : compte UNE visite par jour.

    Seul état réellement non recalculable (une visite passée ne laisse pas de
    trace dérivable). Mise à jour idempotente : un seul incrément par journée.
    """
    pid = p["id"]
    pd = dict(p)
    today = local_today().strftime("%Y-%m-%d")
    last = pd.get("last_visit_date") or ""
    if last == today:
        return
    cur = pd.get("visit_streak") or 0
    if last:
        try:
            consecutive = (date.fromisoformat(today) - date.fromisoformat(last)).days == 1
        except ValueError:
            consecutive = False
        streak = cur + 1 if consecutive else 1
    else:
        streak = 1
    best = max(pd.get("best_visit_streak") or 0, streak)
    await db.execute(
        "UPDATE participants SET last_visit_date=?, visit_streak=?, best_visit_streak=? WHERE id=?",
        (today, streak, best, pid),
    )
    await db.commit()


async def _get_participant_context(token: str, db, active_nav: str = "home") -> dict:
    """Build common context for participant templates."""
    p = await require_participant(token)
    await _record_daily_visit(db, p)
    # Get rank + total points
    rankings = await get_rankings(db)
    rank = next((r for r in rankings if r["id"] == p["id"]), None)
    total_points = rank["total_points"] if rank else 0
    user_rank = rank["rank"] if rank else "—"
    # Pending bonus questions
    bonus_row = await db.execute(
        """SELECT COUNT(*) as cnt FROM bonus_questions bq
           WHERE bq.is_published=1
             AND bq.deadline > ? AND NOT EXISTS (
             SELECT 1 FROM bonus_answers ba
             WHERE ba.question_id = bq.id AND ba.participant_id = ?
           )""",
        (_now_utc(), p["id"])
    )
    pending_bonus = (await bonus_row.fetchone())["cnt"]
    # Story des nouveautés : items publiés non encore vus (home uniquement).
    news_story = []
    if active_nav == "home":
        last_seen = dict(p).get("last_seen_news_id", 0) or 0
        news_rows = await db.execute(
            """SELECT id, slug, title, body, icon, media_path, template_key FROM news_items
               WHERE is_published=1 AND id > ? ORDER BY sort_order, id""",
            (last_seen,),
        )
        news_story = []
        for r in await news_rows.fetchall():
            item = dict(r)
            # Whitelist : on ne garde un template_key que s'il est reconnu.
            if item.get("template_key") not in STORY_TEMPLATES:
                item["template_key"] = None
            news_story.append(item)
    return {
        "participant": dict(p),
        "display_name": _display_name(dict(p)),
        "greeting_name": _greeting_name(dict(p)),
        "total_points": total_points,
        "user_rank": user_rank,
        "user_rank_label": _ordinal_fr(user_rank),
        "pending_bonus": pending_bonus,
        "active_nav": active_nav,
        "page_wide": True,
        "token": token,
        "client_now_ts": int(now_utc().timestamp()),
        "news_story": news_story,
    }


async def _reveal_rank_evolution(db, participant_id: int, window_ids: list,
                                 through_day: str | None = None) -> dict:
    """Évolution de rang provoquée par les matchs du périmètre, focus participant.

    Avant = classement actuel privé des points gagnés sur ces matchs (déterministe,
    sans état stocké). Renvoie les extraits 3 lignes (dessus/toi/dessous) avant/après.
    """
    current = await get_rankings(db)
    if not current or not window_ids:
        return {}
    placeholders = ",".join("?" for _ in window_ids)
    pts_rows = await db.execute(
        f"""SELECT participant_id, COALESCE(SUM(points), 0) AS pts
            FROM scores WHERE match_id IN ({placeholders}) GROUP BY participant_id""",
        window_ids,
    )
    win_pts = {r["participant_id"]: r["pts"] for r in await pts_rows.fetchall()}
    after_points = {r["id"]: r["total_points"] for r in current}
    if through_day:
        future_rows = await db.execute(
            """SELECT s.participant_id, s.points, m.match_date, m.kickoff_time
               FROM scores s JOIN matches m ON m.id=s.match_id
               WHERE s.match_id IS NOT NULL"""
        )
        for row in await future_rows.fetchall():
            item = dict(row)
            if sporting_day(item) > through_day and item["participant_id"] in after_points:
                after_points[item["participant_id"]] -= item["points"]
    baseline_points = {pid: points - win_pts.get(pid, 0) for pid, points in after_points.items()}
    all_bp = sorted(baseline_points.values(), reverse=True)
    before_rank = {pid: 1 + sum(1 for p in all_bp if p > bp) for pid, bp in baseline_points.items()}

    pos = {r["id"]: i for i, r in enumerate(current)}
    before_order = sorted(current, key=lambda r: (-baseline_points[r["id"]], pos[r["id"]]))
    all_ap = sorted(after_points.values(), reverse=True)
    after_rank = {pid: 1 + sum(1 for p in all_ap if p > ap) for pid, ap in after_points.items()}
    after_order = sorted(current, key=lambda r: (-after_points[r["id"]], pos[r["id"]]))

    def extract(order, rank_of, points_of):
        idx = next((k for k, r in enumerate(order) if r["id"] == participant_id), None)
        if idx is None:
            return None

        def mk(r):
            return {
                "id": r["id"], "name": r["name"], "avatar_path": r["avatar_path"],
                "rank": rank_of(r), "points": points_of(r), "is_me": r["id"] == participant_id,
            }
        return {
            "above": mk(order[idx - 1]) if idx > 0 else None,
            "me": mk(order[idx]),
            "below": mk(order[idx + 1]) if idx + 1 < len(order) else None,
        }

    before = extract(before_order, lambda r: before_rank[r["id"]], lambda r: baseline_points[r["id"]])
    after = extract(after_order, lambda r: after_rank[r["id"]], lambda r: after_points[r["id"]])
    me_before = before_rank.get(participant_id)
    me_after = after_rank.get(participant_id)
    moved = me_before is not None and me_after is not None and me_before != me_after

    # Contexte 5 slots (2 dessus + MOI + 2 dessous) avant et après le mouvement.
    # MOI reste au centre ; les 4 slots autour glissent vers leurs nouveaux voisins.
    def get_ctx(order, rank_of, points_of):
        idx = next((k for k, r in enumerate(order) if r["id"] == participant_id), None)
        if idx is None:
            return [None] * 5
        result = []
        for offset in (-2, -1, 0, 1, 2):
            j = idx + offset
            if 0 <= j < len(order):
                r = order[j]
                result.append({
                    "id": r["id"], "name": r["name"], "avatar_path": r["avatar_path"],
                    "rank": rank_of(r), "points": points_of(r),
                    "is_me": r["id"] == participant_id,
                })
            else:
                result.append(None)
        return result

    current_rank_map = after_rank
    current_pts_map = after_points
    before_ctx = get_ctx(before_order, lambda r: before_rank[r["id"]], lambda r: baseline_points[r["id"]])
    after_ctx  = get_ctx(after_order, lambda r: current_rank_map[r["id"]], lambda r: current_pts_map[r["id"]])
    me_before_points = baseline_points.get(participant_id, 0)
    me_after_points  = current_pts_map.get(participant_id, 0)

    return {
        "before": before, "after": after,
        "before_rank": me_before, "after_rank": me_after,
        "moved": moved,
        "delta": (me_before - me_after) if (me_before and me_after) else 0,
        "before_ctx": before_ctx, "after_ctx": after_ctx,
        "me_before_points": me_before_points, "me_after_points": me_after_points,
    }


async def _register_daily_connection(db, participant_id: int) -> str:
    """Fige la connexion précédente au premier accueil de la journée sportive."""
    day = current_sporting_day()
    row = await (await db.execute(
        """SELECT last_connected_sporting_day, reveal_connection_baseline_day,
                  last_revealed_date
           FROM participants WHERE id=?""",
        (participant_id,),
    )).fetchone()
    if not row:
        return day
    if row["last_connected_sporting_day"] == day:
        return row["reveal_connection_baseline_day"] or row["last_revealed_date"] or day
    baseline = row["last_connected_sporting_day"] or row["last_revealed_date"] or day
    await db.execute(
        """UPDATE participants
           SET reveal_connection_baseline_day=?, last_connected_sporting_day=?
           WHERE id=?""",
        (baseline, day, participant_id),
    )
    await db.commit()
    return baseline


async def _reveal_window_data(db, participant_id: int) -> dict | None:
    """Journées finalisées depuis la connexion précédente, dernière garantie."""
    prow = await (await db.execute(
        """SELECT last_revealed_date, reveal_connection_baseline_day,
                  last_connected_sporting_day
           FROM participants WHERE id=?""",
        (participant_id,),
    )).fetchone()
    last_revealed = (prow["last_revealed_date"] if prow else None) or ""
    baseline = (prow["reveal_connection_baseline_day"] if prow else None) or last_revealed

    rows = await db.execute("SELECT * FROM matches ORDER BY match_date, kickoff_time")
    all_matches = [dict(m) for m in await rows.fetchall()]
    for m in all_matches:
        m["sd"] = sporting_day(m)

    by_day = defaultdict(list)
    for match in all_matches:
        by_day[match["sd"]].append(match)
    finalized_days = sorted(
        day for day, matches in by_day.items()
        if matches and all(m["result"] is not None for m in matches)
    )
    if not finalized_days:
        return None
    latest_sd = finalized_days[-1]
    selected_days = [
        day for day in finalized_days
        if day > baseline and day > last_revealed
    ]
    # Le dernier Reveal finalisé reste disponible jusqu'au suivant, sauf s'il a été vu.
    if latest_sd > last_revealed and latest_sd not in selected_days:
        selected_days.append(latest_sd)
    selected_days.sort()
    if not selected_days:
        return None

    window = sorted(
        [m for m in all_matches if m["sd"] in selected_days],
        key=lambda m: (m["match_date"], m["kickoff_time"], m["match_number"]),
    )
    window_ids = [m["id"] for m in window]
    placeholders = ",".join("?" for _ in window_ids)
    mrows = await db.execute(
        f"""SELECT m.id AS mid, pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                   pr.qualifier_prediction, s.points
            FROM matches m
            LEFT JOIN predictions pr ON pr.match_id = m.id AND pr.participant_id = ?
            LEFT JOIN scores s ON s.match_id = m.id AND s.participant_id = ?
            WHERE m.id IN ({placeholders})""",
        (participant_id, participant_id, *window_ids),
    )
    extra = {r["mid"]: dict(r) for r in await mrows.fetchall()}
    matches = []
    total = 0
    exact_count = 0
    for m in window:
        e = extra.get(m["id"], {})
        row = {**m, **e}
        row["points"] = e.get("points") or 0
        row["has_prediction"] = (
            e.get("prediction") is not None or e.get("exact_score_team1") is not None
        )
        row["tier"] = _prediction_tier(row, row)
        row["zero_reason"] = _zero_points_reason(row, row) if row["tier"] == "wrong" else ""
        _enrich_knockout_result_labels(row)
        _enrich_sporting_day_match(row, row["sd"])
        matches.append(row)
        total += row["points"]
        if row["tier"] == "exact":
            exact_count += 1

    day_groups = []
    for day in selected_days:
        day_matches = [m for m in matches if m["sd"] == day]
        day_groups.append({
            "sporting_day": day,
            "day_label": format_sporting_day_fr(day),
            "matches": day_matches,
            "match_count": len(day_matches),
            "total_points": sum(m["points"] for m in day_matches),
            "exact_count": sum(1 for m in day_matches if m["tier"] == "exact"),
        })
    reveal_sd = selected_days[-1]
    is_catchup = len(selected_days) > 1
    return {
        "sporting_day": reveal_sd,
        "day_label": "Depuis ta dernière visite" if is_catchup else format_sporting_day_fr(reveal_sd),
        "days": day_groups,
        "day_count": len(day_groups),
        "is_catchup": is_catchup,
        "matches": matches,
        "total_points": total,
        "exact_count": exact_count,
        "match_count": len(matches),
        "evolution": await _reveal_rank_evolution(
            db, participant_id, window_ids, through_day=reveal_sd
        ),
    }


@router.get("/p/{token}/reveal", response_class=HTMLResponse)
async def reveal_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "home")
        p = ctx["participant"]
        # Le « vu » est marqué en fin de séquence via POST /api/reveal/seen, pas au load.
        ctx["reveal"] = await _reveal_window_data(db, p["id"])
    return templates.TemplateResponse(request, "reveal.html", {"request": request, **ctx})


# ---- Login / registration ----

@router.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    # Reconnexion automatique : la PWA démarre sur "/", le cookie ramène
    # directement le participant chez lui sans repasser par le login.
    cookie_token = request.cookies.get(PARTICIPANT_COOKIE)
    if cookie_token:
        participant = await get_participant_by_token(cookie_token)
        if participant:
            return RedirectResponse(url=f"/p/{cookie_token}", status_code=303)
    response = templates.TemplateResponse(request, "login.html", {
        "request": request, "error": None, "email": ""
    })
    if cookie_token:
        # Token périmé (participant supprimé, base réinitialisée…) : on purge.
        clear_participant_cookie(response)
    return response


@router.post("/connexion", response_class=HTMLResponse)
async def login_post(
    request: Request,
    email: str = Form(default=""),
    password: str = Form(default=""),
):
    email = email.strip().lower()

    def login_error(message: str):
        return templates.TemplateResponse(request, "login.html", {
            "request": request, "error": message, "email": email
        })

    if not email or not password:
        return login_error("Email et mot de passe requis.")
    async with get_db() as db:
        row = await db.execute(
            "SELECT token, password_hash FROM participants WHERE email=? AND is_admin=0",
            (email,),
        )
        participant = await row.fetchone()
    if not participant:
        return login_error("Email ou mot de passe incorrect.")
    if not participant["password_hash"]:
        return login_error(
            "Ce compte n'a pas encore de mot de passe. Utilise ton lien personnel "
            "(reçu par email) — tu pourras en créer un depuis ton profil."
        )
    if not verify_password(password, participant["password_hash"]):
        return login_error("Email ou mot de passe incorrect.")
    response = RedirectResponse(url=f"/p/{participant['token']}", status_code=303)
    set_participant_cookie(
        response, participant["token"], secure=request.url.scheme == "https"
    )
    return response


@router.post("/deconnexion")
async def participant_logout(request: Request):
    """Oublie ce compte sur cet appareil (efface le cookie de reconnexion)."""
    response = RedirectResponse(url="/", status_code=303)
    clear_participant_cookie(response)
    return response


@router.get("/lien-perdu", response_class=HTMLResponse)
async def lost_link_page(request: Request):
    return templates.TemplateResponse(request, "lost_link.html", {
        "request": request, "sent": False, "email": ""
    })


@router.post("/lien-perdu", response_class=HTMLResponse)
async def lost_link_post(request: Request, email: str = Form(default="")):
    email = email.strip().lower()
    if email and "@" in email:
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM participants WHERE email=? AND is_admin=0", (email,)
            )
            participant = await row.fetchone()
        if participant:
            await send_invitation(dict(participant))
        else:
            logger.info("Lien perdu demandé pour un email inconnu: %s", email)
    # Réponse neutre: ne révèle pas si l'email est inscrit.
    return templates.TemplateResponse(request, "lost_link.html", {
        "request": request, "sent": True, "email": email
    })


def _register_context(error=None, **form):
    return {
        "error": error,
        "departments": DEPARTMENTS,
        "form": {
            "first_name": form.get("first_name", ""),
            "last_name": form.get("last_name", ""),
            "email": form.get("email", ""),
            "department": form.get("department", ""),
        },
    }


@router.get("/rejoindre", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {
        "request": request, **_register_context()
    })


@router.post("/rejoindre", response_class=HTMLResponse)
async def register_post(
    request: Request,
    first_name: str = Form(default=""),
    last_name: str = Form(default=""),
    email: str = Form(default=""),
    department: str = Form(default=""),
    password: str = Form(default=""),
    password_confirm: str = Form(default=""),
):
    first_name, last_name, name = build_full_name(first_name[:80], last_name[:80])
    email = email.strip().lower()
    department = department.strip()

    def register_error(message: str):
        return templates.TemplateResponse(request, "register.html", {
            "request": request,
            **_register_context(message, first_name=first_name, last_name=last_name,
                                email=email, department=department),
        })

    if not first_name or not last_name:
        return register_error("Prénom et nom requis.")
    if not email or "@" not in email:
        return register_error("Email valide requis.")
    if department not in DEPARTMENTS:
        return register_error("Choisis ton département RESA.")
    if len(password) < MIN_PASSWORD_LENGTH:
        return register_error(f"Le mot de passe doit faire au moins {MIN_PASSWORD_LENGTH} caractères.")
    if password != password_confirm:
        return register_error("Les deux mots de passe ne correspondent pas.")

    token = str(uuid.uuid4())
    registration_day = current_sporting_day()
    registration_previous_day = (
        date.fromisoformat(registration_day) - timedelta(days=1)
    ).isoformat()
    async with get_db() as db:
        existing = await (await db.execute(
            "SELECT id FROM participants WHERE email=?", (email,)
        )).fetchone()
        if existing:
            return register_error(
                "Cet email est déjà inscrit. Connecte-toi avec ton mot de passe, "
                "ou utilise ton lien personnel reçu par email."
            )
        try:
            await db.execute(
                """INSERT INTO participants
                   (name, first_name, last_name, email, token, is_confirmed,
                    password_hash, department, last_connected_sporting_day,
                    reveal_connection_baseline_day, last_revealed_date)
                   VALUES (?,?,?,?,?,1,?,?,?,?,?)""",
                (name, first_name, last_name, email, token,
                 hash_password(password), department,
                 registration_day, registration_day, registration_previous_day)
            )
            await db.commit()
        except Exception:
            return register_error("Une erreur est survenue, réessaie.")
    return RedirectResponse(url=f"/p/{token}", status_code=303)


@router.get("/p/{token}", response_class=HTMLResponse)
async def participant_home(request: Request, token: str):
    p = await require_participant(token)
    if not p["is_confirmed"]:
        return templates.TemplateResponse(request, "onboarding.html", {
            "request": request, "participant": dict(p), "token": token,
            "departments": DEPARTMENTS,
        })
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "home")
        await _register_daily_connection(db, p["id"])
        # Reveal du jour : proposé seulement quand le lot/nuit est complet (cf.
        # _reveal_window_data, qui renvoie None tant qu'on attend l'encodage).
        reveal = await _reveal_window_data(db, p["id"])
        ctx["reveal_available"] = reveal is not None
        ctx["reveal_points"] = reveal["total_points"] if reveal else 0
        ctx["reveal_day_label"] = reveal["day_label"] if reveal else ""
        ctx["reveal_day_count"] = reveal["day_count"] if reveal else 0
        ctx["reveal_is_catchup"] = reveal["is_catchup"] if reveal else False
        # Encart récap = miroir EXACT du Reveal (même fenêtre journée sportive et
        # même baseline) : points, compte et flèche dérivent du même objet, donc
        # toujours cohérents. Se cache en même temps que le Reveal (reveal_available).
        ctx["reveal_match_count"] = reveal["match_count"] if reveal else 0
        ctx["reveal_evolution"] = (
            reveal["evolution"]["delta"]
            if reveal and reveal.get("evolution") and reveal["evolution"].get("after")
            else None
        )
        # Matchs de la journée sportive locale (9 h → lendemain 8 h 59).
        home_sporting_day = current_sporting_day()
        today_start_utc, today_end_utc = sporting_day_bounds(home_sporting_day)
        rows = await db.execute(
            """SELECT m.*,
                 p.prediction, p.exact_score_team1, p.exact_score_team2,
                 p.qualifier_prediction,
                 s.points
               FROM matches m
               LEFT JOIN predictions p ON p.match_id = m.id AND p.participant_id = ?
               LEFT JOIN scores s ON s.match_id = m.id AND s.participant_id = ?
               WHERE datetime(m.match_date || 'T' || m.kickoff_time) >= datetime(?)
                 AND datetime(m.match_date || 'T' || m.kickoff_time) < datetime(?)
               ORDER BY m.match_date, m.kickoff_time""",
            (p["id"], p["id"], today_start_utc, today_end_utc)
        )
        today_matches = [dict(r) for r in await rows.fetchall()]
        for m in today_matches:
            m["is_locked"] = _is_locked(m)
            m["pronos_closed"] = not match_predictions_open(m)
            m["live_state"] = _live_state(m)
            m["tier"] = _prediction_tier(m, m)
            _enrich_sporting_day_match(m, home_sporting_day)
        # Urgency match: next unpredicted/locked-soon match
        urgency = None
        for m in today_matches:
            mins = _minutes_until(m)
            if (
                not m["is_locked"]
                and not m.get("pronos_closed")
                and m.get("prediction") is None
                and 0 < mins < 300
            ):
                m["mins_until"] = mins
                m["kickoff_ts"] = int(match_kickoff_utc(m).timestamp())
                urgency = m
                break
        # Unmatched today alert
        unpredicted_today = sum(
            1 for m in today_matches
            if not m["is_locked"] and not m.get("pronos_closed") and not m.get("prediction")
        )
        # Next upcoming match (today or later) for the "all caught up" state
        next_row = await db.execute(
            """SELECT m.*, p.prediction
               FROM matches m
               LEFT JOIN predictions p ON p.match_id = m.id AND p.participant_id = ?
               WHERE datetime(m.match_date || 'T' || m.kickoff_time) > datetime(?)
               ORDER BY m.match_date, m.kickoff_time LIMIT 1""",
            (p["id"], _now_utc())
        )
        next_match = await next_row.fetchone()
        next_match = dict(next_match) if next_match else None
        if next_match:
            next_match["kickoff_ts"] = int(match_kickoff_utc(next_match).timestamp())
        next_match_in_today = bool(
            next_match and any(m["id"] == next_match["id"] for m in today_matches)
        )
        # Mini ranking (top 3 + self) + nearest rivals
        rankings = await get_rankings(db)
        mini_rank = rankings[:3]
        me_rank = next((r for r in rankings if r["id"] == p["id"]), None)
        if me_rank and me_rank["rank"] > 3 and me_rank not in mini_rank:
            mini_rank.append(me_rank)
        rival_ahead = None
        rival_behind = None
        if me_rank:
            my_points = me_rank["total_points"]
            for r in rankings:
                if r["total_points"] > my_points:
                    rival_ahead = {"name": r["name"], "gap": r["total_points"] - my_points}
                elif r["total_points"] < my_points and rival_behind is None:
                    rival_behind = {"name": r["name"], "gap": my_points - r["total_points"]}
                    break
        # (Le récap « nuit » dérive désormais du Reveal — cf. reveal_* plus haut —
        # pour rester aligné sur la journée sportive et cohérent avec l'animation.)
        # Pre-tournament status
        pt_status = await _pt_status(db, p["id"])
        all_caught_up = (
            urgency is None
            and unpredicted_today == 0
            and (not pt_status["open"] or pt_status["complete"])
            and ctx["pending_bonus"] == 0
        )
        tc_row = await db.execute(
            "SELECT COUNT(DISTINCT trophy_key) AS cnt FROM trophy_awards WHERE participant_id=?",
            (p["id"],),
        )
        trophy_unlocked_count = (await tc_row.fetchone())["cnt"]
        ctx.update({
            "trophy_unlocked_count": trophy_unlocked_count,
            "today_matches": today_matches,
            "sporting_day_label": format_sporting_day_fr(home_sporting_day),
            "sporting_day_help": (
                f"Du {format_sporting_day_fr(home_sporting_day)} à 9 h au "
                f"{format_sporting_day_fr((date.fromisoformat(home_sporting_day) + timedelta(days=1)).isoformat())} à 8 h 59"
            ),
            "urgency": urgency,
            "unpredicted_today": unpredicted_today,
            "next_match": next_match,
            "next_match_in_today": next_match_in_today,
            "all_caught_up": all_caught_up,
            "mini_rank": mini_rank,
            "rival_ahead": rival_ahead,
            "rival_behind": rival_behind,
            "pt_complete": pt_status["complete"],
            "pt_filled_count": pt_status["filled_count"],
            "pt_question_count": pt_status["question_count"],
            "pt_open": pt_status["open"],
            "pt_deadline": pt_status["deadline"],
        })
    return templates.TemplateResponse(request, "home.html", {"request": request, **ctx})


@router.post("/p/{token}/confirm", response_class=HTMLResponse)
async def confirm_onboarding(request: Request, token: str,
                              first_name: str = Form(...), last_name: str = Form(...),
                              department: str = Form(default="")):
    p = await require_participant(token)
    first_name, last_name, name = build_full_name(first_name[:80], last_name[:80], p["name"])
    department = department.strip()
    if department not in DEPARTMENTS:
        department = ""
    async with get_db() as db:
        onboarding_day = current_sporting_day()
        onboarding_previous_day = (
            date.fromisoformat(onboarding_day) - timedelta(days=1)
        ).isoformat()
        await db.execute(
            """UPDATE participants
               SET name = ?, first_name = ?, last_name = ?, is_confirmed = 1,
                   department = COALESCE(NULLIF(?, ''), department),
                   last_connected_sporting_day=COALESCE(last_connected_sporting_day, ?),
                   reveal_connection_baseline_day=COALESCE(reveal_connection_baseline_day, ?),
                   last_revealed_date=COALESCE(last_revealed_date, ?)
               WHERE token = ?""",
            (name, first_name, last_name, department, onboarding_day,
             onboarding_day, onboarding_previous_day, token)
        )
        await db.commit()
    return RedirectResponse(url=f"/p/{token}", status_code=303)


@router.get("/p/{token}/pronos", response_class=HTMLResponse)
async def predictions_page(request: Request, token: str, section: str = "",
                           phase: str = "", match: int = 0):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "pronos")
        p = ctx["participant"]
        rows = await db.execute(
            """SELECT m.*,
                 pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                 pr.qualifier_prediction, s.points
               FROM matches m
               LEFT JOIN predictions pr ON pr.match_id = m.id AND pr.participant_id = ?
               LEFT JOIN scores s ON s.match_id = m.id AND s.participant_id = ?
               ORDER BY m.match_date, m.kickoff_time""",
            (p["id"], p["id"])
        )
        all_matches = [dict(r) for r in await rows.fetchall()]
        _enrich_prediction_matches(all_matches)
        for match in all_matches:
            match["pronos_closed"] = not match_predictions_open(match)
        sections = _prediction_sections(all_matches)
        requested_section = section
        if not requested_section and match:
            # Lien profond depuis la home: ouvrir la section du match visé.
            target = next((m for m in all_matches if m["id"] == match), None)
            if target:
                requested_section = target.get("section_key") or ""
        if not requested_section and phase:
            requested_section = "group_match_1" if phase == "group" else phase
        if requested_section not in PREDICTION_SECTION_ORDER:
            requested_section = _default_prediction_section(sections)
        current_matches = [
            match for match in all_matches
            if match.get("section_key") == requested_section
        ]
        current_section_closed = bool(
            current_matches
            and all(
                m.get("pronos_closed") or m.get("is_locked")
                for m in current_matches
                if not m.get("is_locked")
            )
            and any(m.get("pronos_closed") for m in current_matches)
        )
        any_knockout_open = any(
            m["phase"] != "group" and match_predictions_open(m)
            for m in all_matches
        )
        pt_status = await _pt_status(db, p["id"])
        ctx.update({
            "matches": all_matches,
            "current_matches": current_matches,
            "current_section": requested_section,
            "current_section_label": PREDICTION_SECTION_LABELS.get(requested_section, requested_section),
            "current_section_help": _section_help(requested_section),
            "prediction_sections": sections,
            "phase_labels": PHASE_LABELS,
            "pt_complete": pt_status["complete"],
            "pt_filled_count": pt_status["filled_count"],
            "pt_question_count": pt_status["question_count"],
            "pt_open": pt_status["open"],
            "knockout_open": any_knockout_open,
            "current_section_closed": current_section_closed,
            "page_wide": True,
        })
    return templates.TemplateResponse(request, "predictions.html", {"request": request, **ctx})


@router.get("/p/{token}/reglement", response_class=HTMLResponse)
async def rules_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "rules")
        ctx["page_wide"] = True
        ctx["prize_info"] = await get_prize_info(db)
        open_row = await db.execute(
            "SELECT COUNT(*) AS cnt FROM matches WHERE phase!='group' AND predictions_open=1"
        )
        ctx["knockout_open"] = (await open_row.fetchone())["cnt"] > 0
    return templates.TemplateResponse(request, "rules.html", {"request": request, **ctx})


PT_ERRORS = {
    "winner_finalist": "L'autre finaliste doit être différent du champion.",
    "invalid_team": "Une des équipes choisies est invalide.",
    "invalid_scorer": "Le joueur choisi est invalide.",
}


@router.get("/p/{token}/pre-tournoi", response_class=HTMLResponse)
async def pre_tournament_page(request: Request, token: str, error: str = "", saved: str = ""):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "bonus")
        p = ctx["participant"]
        outsiders = OUTSIDERS
        pt_status = await _pt_status(db, p["id"])
        pt_deadline = pt_status["deadline"]
        pt_questions = await get_pre_tournament_question_map(db)
        pt_editable = pt_status["open"]
        pt_dict = pt_status["pt"]
        if pt_dict.get("top_scorer"):
            pt_dict["top_scorer"] = normalize_scorer(pt_dict["top_scorer"])
        # Results once correct answers are encoded (after deadline)
        score_rows = await db.execute(
            "SELECT question_key, points FROM pre_tournament_scores WHERE participant_id=?",
            (p["id"],),
        )
        pt_scores = {r["question_key"]: r["points"] for r in await score_rows.fetchall()}
        ctx.update({
            "pt": pt_dict,
            "pt_questions": pt_questions,
            "teams": TEAMS_48,
            "scorer_options": get_scorer_options(),
            "outsiders": outsiders,
            "pt_editable": pt_editable,
            "pt_deadline": pt_deadline,
            "pt_scores": pt_scores,
            "pt_error": PT_ERRORS.get(error),
            "pt_saved": saved == "1",
            "pt_filled_count": pt_status["filled_count"],
            "pt_question_count": pt_status["question_count"],
            "pt_complete": pt_status["complete"],
        })
    return templates.TemplateResponse(request, "pre_tournament.html", {"request": request, **ctx})


@router.post("/p/{token}/pre-tournoi", response_class=HTMLResponse)
async def save_pre_tournament(
    request: Request, token: str,
    winner: str = Form(default=""),
    finalist: str = Form(default=""),
    top_scorer: str = Form(default=""),
    revelation: str = Form(default=""),
    total_goals: int = Form(default=0),
):
    p = await require_participant(token)
    winner = winner.strip()
    finalist = finalist.strip()
    top_scorer = top_scorer.strip()
    revelation = revelation.strip()
    if winner and finalist and winner == finalist:
        return RedirectResponse(
            url=f"/p/{token}/pre-tournoi?error=winner_finalist", status_code=303
        )
    for team_value in (winner, finalist):
        if team_value and team_value not in TEAMS_48:
            return RedirectResponse(
                url=f"/p/{token}/pre-tournoi?error=invalid_team", status_code=303
            )
    if revelation and revelation not in OUTSIDERS:
        return RedirectResponse(
            url=f"/p/{token}/pre-tournoi?error=invalid_team", status_code=303
        )
    if top_scorer and not is_valid_scorer(top_scorer):
        return RedirectResponse(
            url=f"/p/{token}/pre-tournoi?error=invalid_scorer", status_code=303
        )
    async with get_db() as db:
        pt_deadline = await get_pre_tournament_deadline(db)
        if _now_utc() >= pt_deadline:
            return RedirectResponse(url=f"/p/{token}/pre-tournoi", status_code=303)
        enabled = {
            q["key"] for q in await get_pre_tournament_questions(db)
        }
        existing = await (await db.execute(
            "SELECT * FROM pre_tournament_predictions WHERE participant_id = ?", (p["id"],)
        )).fetchone()
        values = {
            "winner": existing["winner"] if existing else "",
            "finalist": existing["finalist"] if existing else "",
            "top_scorer": existing["top_scorer"] if existing else "",
            "revelation": existing["revelation"] if existing else "",
            "total_goals": existing["total_goals"] if existing else 0,
        }
        incoming = {
            "winner": winner,
            "finalist": finalist,
            "top_scorer": top_scorer,
            "revelation": revelation,
            "total_goals": total_goals,
        }
        for key in enabled:
            values[key] = incoming[key]
        # Toute sauvegarde compte pour les points : pas de notion de brouillon.
        submitted_at = _now_utc()
        if existing:
            await db.execute(
                """UPDATE pre_tournament_predictions
                   SET winner=?, finalist=?, top_scorer=?, revelation=?, total_goals=?,
                       submitted=1, submitted_at=?
                   WHERE participant_id=?""",
                (values["winner"], values["finalist"], values["top_scorer"], values["revelation"], values["total_goals"],
                 submitted_at, p["id"])
            )
        else:
            await db.execute(
                """INSERT INTO pre_tournament_predictions
                   (participant_id, winner, finalist, top_scorer, revelation, total_goals, submitted, submitted_at)
                   VALUES (?,?,?,?,?,?,1,?)""",
                (p["id"], values["winner"], values["finalist"], values["top_scorer"], values["revelation"], values["total_goals"], submitted_at)
            )
        await db.commit()
    return RedirectResponse(url=f"/p/{token}/pre-tournoi?saved=1", status_code=303)


RANKING_VIEWS = {
    "general": "Général",
    "groups": "Groupes",
    "knockout": "Phase finale",
    "bonus": "Bonus",
    "remontada": "Remontada",
    "departments": "Départements",
}

RANKING_INDIVIDUAL_VIEWS = {
    "general": "Général",
    "groups": "Groupes",
    "knockout": "Phase finale",
    "bonus": "Bonus uniquement",
    "remontada": "Remontada",
}

DEPARTMENT_PREVIEW_LIMIT = 5
DEPARTMENT_COLLAPSE_THRESHOLD = 6


def _format_day_fr(value: str) -> str:
    return format_sporting_day_fr(value)


async def _sporting_day_match_details(db, day: str) -> list[dict]:
    """Résultats officiels inclus dans une journée sportive, par kickoff."""
    start_utc, end_utc = sporting_day_bounds(day)
    rows = await db.execute(
        """SELECT team1_name, team2_name, match_date, kickoff_time, phase,
                  score_team1, score_team2, final_score_team1, final_score_team2,
                  result, qualifier_winner
           FROM matches
           WHERE datetime(match_date || 'T' || kickoff_time) >= datetime(?)
             AND datetime(match_date || 'T' || kickoff_time) < datetime(?)
             AND result IS NOT NULL
           ORDER BY match_date, kickoff_time""",
        (start_utc, end_utc),
    )
    matches = []
    for row in await rows.fetchall():
        match = dict(row)
        match["qualifier_name"] = (
            match["team1_name"] if match.get("qualifier_winner") == "team1"
            else match["team2_name"] if match.get("qualifier_winner") == "team2"
            else ""
        )
        matches.append(match)
    return matches


@router.get("/p/{token}/classement", response_class=HTMLResponse)
async def ranking_page(
    request: Request,
    token: str,
    view: str = "general",
    department: str = "",
    members: str = "",
):
    if view not in RANKING_VIEWS:
        view = "general"
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "rank")
        p = ctx["participant"]
        rankings = []
        departments = []
        evolution = {}
        evolution_day = None
        evolution_status = None
        evolution_match_count = 0
        evolution_encoded_count = 0
        evolution_last_match = None
        climber_ids = set()
        carousel_items: list[dict] = []
        badges_by_participant: dict[int, list[dict]] = {}
        knockout_started = False
        if view == "remontada":
            rankings = await get_remontada(db)
            ko_row = await db.execute(
                "SELECT COUNT(*) AS cnt FROM matches WHERE phase != 'group' AND result IS NOT NULL"
            )
            knockout_started = (await ko_row.fetchone())["cnt"] > 0
        elif view == "departments":
            departments = await get_department_rankings(db)
        else:
            rankings = await get_rankings(db, scope=view)
            if view == "general":
                evo = await get_rank_evolution(db)
                evolution = evo["deltas"]
                evolution_day = evo["day"]
                evolution_status = evo["status"]
                evolution_match_count = evo["match_count"]
                evolution_encoded_count = evo["encoded_count"]
                if evolution_status == "in_progress" and evolution_day:
                    encoded_matches = await _sporting_day_match_details(db, evolution_day)
                    evolution_last_match = encoded_matches[-1] if encoded_matches else None
                finalized_climbers = await get_latest_finalized_climbers(db)
                climber_ids = {r["participant_id"] for r in finalized_climbers["climbers"]}
            carousel_items, badges_by_participant = await all_ephemeral_badges(db, current_participant_id=p["id"])
        for r in rankings:
            r["is_me"] = (r["id"] == p["id"])
            r["color_class"] = f"c{((r['id'] - 1) % 8) + 1}"
            r["evolution"] = evolution.get(r["id"])
            r["is_climber"] = r["id"] in climber_ids
            r["badges"] = badges_by_participant.get(r["id"], [])
        prize_info = await get_prize_info(db)
        my_department = (p.get("department") or "").strip() or "Sans département"
        department_names = {d["department"] for d in departments}
        open_department = department.strip() if department.strip() in department_names else ""
        members_all = members == "all" and bool(open_department)
        ctx.update({
            "rankings": rankings,
            "departments": departments,
            "my_department": my_department,
            "view": view,
            "ranking_mode": "departments" if view == "departments" else "individual",
            "individual_view": view if view in RANKING_INDIVIDUAL_VIEWS else "general",
            "individual_views": RANKING_INDIVIDUAL_VIEWS,
            "open_department": open_department,
            "members_all": members_all,
            "department_preview_limit": DEPARTMENT_PREVIEW_LIMIT,
            "department_collapse_threshold": DEPARTMENT_COLLAPSE_THRESHOLD,
            "evolution_day_label": _format_day_fr(evolution_day) if evolution_day else "",
            "evolution_status": evolution_status,
            "evolution_match_count": evolution_match_count,
            "evolution_encoded_count": evolution_encoded_count,
            "evolution_last_match": evolution_last_match,
            "carousel_items": carousel_items,
            "knockout_started": knockout_started,
            "prize_info": prize_info,
        })
    return templates.TemplateResponse(request, "ranking.html", {"request": request, **ctx})


def _fold_name(name: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (name or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def _group_match_predictions(all_preds: list[dict], match: dict) -> list[dict]:
    """Regroupe les pronostics par score prédit (et par qualifié pour un nul KO),
    joueurs triés alphabétiquement dans chaque sous-groupe."""
    is_knockout = match["phase"] != "group"
    done = match.get("result") is not None
    buckets: dict[tuple, list[dict]] = {}
    for pr in all_preds:
        s1, s2 = pr.get("exact_score_team1"), pr.get("exact_score_team2")
        if s1 is None or s2 is None:
            key = ("none", None, None, None)
        else:
            qual = pr.get("qualifier_prediction") if (is_knockout and s1 == s2) else None
            key = ("score", s1, s2, qual)
        buckets.setdefault(key, []).append(pr)

    groups = []
    for (kind, s1, s2, qual), preds in buckets.items():
        if kind == "none":
            label, outcome = "Sans score exact", ""
        else:
            label = f"{s1}–{s2}"
            if s1 > s2:
                outcome = f"Victoire {match['team1_name']}"
            elif s2 > s1:
                outcome = f"Victoire {match['team2_name']}"
            elif qual:
                team = match["team1_name"] if qual == "team1" else match["team2_name"]
                outcome = f"Nul · {team} qualifié"
            else:
                outcome = "Match nul"
        tiers = {pr["tier"] for pr in preds}
        tier = next(iter(tiers)) if len(tiers) == 1 else ""
        points_set = {pr.get("points") or 0 for pr in preds}
        points = next(iter(points_set)) if done and len(points_set) == 1 else None
        preds.sort(key=lambda pr: _fold_name(pr["name"]))
        groups.append({
            "label": label,
            "outcome": outcome,
            "tier": tier,
            "points": points,
            "preds": preds,
            "count": len(preds),
            "has_me": any(pr["is_me"] for pr in preds),
        })

    tier_rank = {"exact": 0, "correct": 1, "wrong": 2, "": 3}
    if done:
        groups.sort(key=lambda g: (tier_rank.get(g["tier"], 3), -g["count"], g["label"]))
    else:
        groups.sort(key=lambda g: (-g["count"], g["label"]))
    return groups


@router.get("/p/{token}/match/{match_id}", response_class=HTMLResponse)
async def match_detail_page(request: Request, token: str, match_id: int):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "pronos")
        p = ctx["participant"]
        row = await db.execute("SELECT * FROM matches WHERE id = ?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        if not _is_locked(dict(match)) and match["result"] is None:
            raise HTTPException(403, "Match pas encore joué")
        # Own prediction
        pr_row = await db.execute(
            "SELECT * FROM predictions WHERE match_id=? AND participant_id=?",
            (match_id, p["id"])
        )
        my_pred = await pr_row.fetchone()
        score_row = await db.execute(
            "SELECT * FROM scores WHERE match_id=? AND participant_id=?",
            (match_id, p["id"])
        )
        my_score = await score_row.fetchone()
        match_dict = dict(match)
        # Community distribution
        if match_dict["phase"] == "group":
            dist_row = await db.execute(
                "SELECT prediction, COUNT(*) as cnt FROM predictions WHERE match_id=? GROUP BY prediction",
                (match_id,)
            )
            dist_raw = {r["prediction"]: r["cnt"] for r in await dist_row.fetchall()}
        else:
            dist_pred_rows = await db.execute(
                """SELECT prediction, exact_score_team1, exact_score_team2, qualifier_prediction
                   FROM predictions WHERE match_id=?""",
                (match_id,)
            )
            dist_raw = defaultdict(int)
            for pred_row in await dist_pred_rows.fetchall():
                winner = predicted_match_winner(dict(pred_row), match_dict)
                if winner:
                    dist_raw[winner] += 1
        dist_total = sum(dist_raw.values())
        total_preds = dist_total or 1
        dist = {k: {"cnt": v, "pct": round(v / total_preds * 100)} for k, v in dist_raw.items()}
        # All predictions (only after kickoff)
        all_preds_rows = await db.execute(
            """SELECT par.id as participant_id, par.name, pr.prediction,
                      pr.exact_score_team1, pr.exact_score_team2,
                      pr.qualifier_prediction, s.points
               FROM predictions pr
               JOIN participants par ON par.id = pr.participant_id
               LEFT JOIN scores s ON s.match_id = pr.match_id AND s.participant_id = pr.participant_id
               WHERE pr.match_id = ?
               ORDER BY COALESCE(s.points, 0) DESC""",
            (match_id,)
        )
        all_preds = [dict(r) for r in await all_preds_rows.fetchall()]
        live_state = _live_state(match_dict)
        for pr in all_preds:
            pr["tier"] = _prediction_tier(pr, match_dict)
            pr["is_me"] = pr["participant_id"] == p["id"]
        pred_groups = _group_match_predictions(all_preds, match_dict)
        tier_stats = {"exact": 0, "correct": 0, "wrong": 0}
        if live_state == "done":
            for pr in all_preds:
                if pr["tier"] in tier_stats:
                    tier_stats[pr["tier"]] += 1
        my_pred_dict = dict(my_pred) if my_pred else None
        my_tier = _prediction_tier(my_pred_dict, match_dict) if my_pred_dict else ""
        # Libellés de qualifié (réel vs choisi) calculés sur la fusion prono + match.
        label_row = {**match_dict, "qualifier_prediction": (my_pred_dict or {}).get("qualifier_prediction")}
        _enrich_knockout_result_labels(label_row)
        ctx.update({
            "match": match_dict,
            "match_phase_label": PHASE_LABELS.get(match["phase"], match["phase"]),
            "live_state": live_state,
            "my_pred": my_pred_dict,
            "my_score": dict(my_score) if my_score else None,
            "my_tier": my_tier,
            "my_zero_reason": _zero_points_reason(my_pred_dict, match_dict) if my_tier == "wrong" else "",
            "my_qualifier_real_label": label_row["qualifier_real_label"],
            "my_qualifier_match": label_row["qualifier_match"],
            "result_is_tiebreak": ("a.p." in (result_full_label(match_dict) or "")) or ("t.a.b." in (result_full_label(match_dict) or "")),
            "my_dist_key": predicted_match_winner(my_pred_dict, match_dict) if my_pred_dict else "",
            "dist": dist,
            "dist_total": dist_total,
            "all_preds": all_preds,
            "pred_groups": pred_groups,
            "tier_stats": tier_stats,
        })
    return templates.TemplateResponse(request, "match_detail.html", {"request": request, **ctx})


def _ranking_return_url(
    token: str,
    return_view: str,
    return_department: str = "",
    return_members: str = "",
) -> str:
    """URL de retour bornée au classement, sans accepter de redirection libre."""
    if return_view not in RANKING_VIEWS:
        return ""
    params = {"view": return_view}
    if return_view == "departments":
        department = return_department.strip()
        if department not in {*DEPARTMENTS, "Sans département"}:
            return ""
        params["department"] = department
        if return_members == "all":
            params["members"] = "all"
    return f"/p/{token}/classement?{urlencode(params)}"


@router.get("/p/{token}/profil", response_class=HTMLResponse)
async def own_profile(
    request: Request,
    token: str,
    return_view: str = "",
    return_department: str = "",
    return_members: str = "",
):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "profil")
        p = ctx["participant"]
        profile_data = await _build_profile(p["id"], db)
        ctx.update({
            "profile": profile_data,
            "is_own": True,
            "profile_back_url": _ranking_return_url(
                token, return_view, return_department, return_members
            ),
        })
    return templates.TemplateResponse(request, "profile.html", {"request": request, **ctx})


PROFILE_EDIT_MESSAGES = {
    "password_set": ("ok", "Mot de passe enregistré — tu peux maintenant te connecter avec ton email."),
    "password_mismatch": ("err", "Les deux mots de passe ne correspondent pas."),
    "password_short": ("err", f"Le mot de passe doit faire au moins {MIN_PASSWORD_LENGTH} caractères."),
    "avatar_invalid": ("err", "Fichier non reconnu — utilise une image JPEG, PNG ou WebP."),
}

AVATARS_DIR = settings.AVATARS_DIR


def _delete_avatar_file(path: str | None) -> None:
    if not path:
        return
    if os.path.basename(path) != path:
        return
    try:
        os.remove(os.path.join(AVATARS_DIR, path))
    except OSError:
        pass


@router.get("/p/{token}/profil/edit", response_class=HTMLResponse)
async def edit_profile_page(request: Request, token: str, msg: str = ""):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "profil")
        ctx.update({
            "teams": TEAMS_48,
            "departments": DEPARTMENTS,
            "edit_message": PROFILE_EDIT_MESSAGES.get(msg),
        })
    return templates.TemplateResponse(request, "profile_edit.html", {"request": request, **ctx})


@router.post("/p/{token}/profil/edit", response_class=HTMLResponse)
async def save_profile(
    request: Request,
    token: str,
    first_name: str = Form(default=""),
    last_name: str = Form(default=""),
    nickname: str = Form(default=""),
    favorite_team: str = Form(default=""),
    department: str = Form(default=""),
    bio: str = Form(default=""),
    profile_visibility: str = Form(default="public"),
    email_opt_in: str = Form(default="0"),
    new_password: str = Form(default=""),
    new_password_confirm: str = Form(default=""),
    avatar: UploadFile = File(None),
    delete_avatar: str = Form(default="0"),
):
    p = dict(await require_participant(token))
    first_name, last_name, full_name = build_full_name(first_name[:80], last_name[:80], p["name"])
    nickname = nickname.strip()[:40]
    favorite_team = favorite_team.strip()[:80]
    department = department.strip()
    if department not in DEPARTMENTS:
        department = ""
    bio = bio.strip()[:240]
    if profile_visibility not in ("public", "limited"):
        profile_visibility = "public"

    password_msg = ""
    password_hash = None
    if new_password or new_password_confirm:
        if len(new_password) < MIN_PASSWORD_LENGTH:
            password_msg = "password_short"
        elif new_password != new_password_confirm:
            password_msg = "password_mismatch"
        else:
            password_hash = hash_password(new_password)
            password_msg = "password_set"

    # Handle avatar upload / deletion
    avatar_new_path = None   # None = no change
    avatar_delete = False
    avatar_msg = ""
    avatar_old_path = p.get("avatar_path")

    if delete_avatar == "1":
        avatar_delete = True
    elif avatar and avatar.filename:
        content = await avatar.read()
        if content:
            try:
                image = Image.open(io.BytesIO(content))
                image = ImageOps.exif_transpose(image)
                image.thumbnail((400, 400))
                if image.mode in ("RGBA", "P", "LA"):
                    bg = Image.new("RGB", image.size, (255, 255, 255))
                    converted = image.convert("RGBA") if image.mode == "P" else image
                    bg.paste(converted, mask=converted.split()[-1])
                    image = bg
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                os.makedirs(AVATARS_DIR, exist_ok=True)
                filename = f"{p['id']}-{uuid.uuid4().hex[:12]}.jpg"
                image.save(os.path.join(AVATARS_DIR, filename), "JPEG", quality=85)
                avatar_new_path = filename
            except Exception:
                avatar_msg = "avatar_invalid"

    async with get_db() as db:
        await db.execute(
            """UPDATE participants
               SET name=?, first_name=?, last_name=?, nickname=?, favorite_team=?,
                   department=?, bio=?, profile_visibility=?, email_opt_in=?
               WHERE token=?""",
            (
                full_name,
                first_name,
                last_name,
                nickname,
                favorite_team,
                department,
                bio,
                profile_visibility,
                1 if email_opt_in == "1" else 0,
                token,
            ),
        )
        if avatar_delete:
            await db.execute("UPDATE participants SET avatar_path=NULL WHERE token=?", (token,))
        elif avatar_new_path:
            await db.execute(
                "UPDATE participants SET avatar_path=? WHERE token=?",
                (avatar_new_path, token),
            )
        if password_hash:
            await db.execute(
                "UPDATE participants SET password_hash=? WHERE token=?",
                (password_hash, token),
            )
        await db.commit()

    if avatar_delete or avatar_new_path:
        _delete_avatar_file(avatar_old_path)

    if avatar_msg:
        return RedirectResponse(url=f"/p/{token}/profil/edit?msg={avatar_msg}", status_code=303)
    if password_msg and password_msg != "password_set":
        return RedirectResponse(url=f"/p/{token}/profil/edit?msg={password_msg}", status_code=303)
    if password_msg == "password_set":
        return RedirectResponse(url=f"/p/{token}/profil/edit?msg=password_set", status_code=303)
    return RedirectResponse(url=f"/p/{token}/profil", status_code=303)


@router.get("/p/{token}/profil/{participant_id}", response_class=HTMLResponse)
async def other_profile(
    request: Request,
    token: str,
    participant_id: int,
    return_view: str = "",
    return_department: str = "",
    return_members: str = "",
):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "")
        p = ctx["participant"]
        row = await db.execute("SELECT * FROM participants WHERE id=? AND is_confirmed=1", (participant_id,))
        target = await row.fetchone()
        if not target:
            raise HTTPException(404)
        profile_data = await _build_profile(participant_id, db, viewer_id=p["id"])
        ctx.update({
            "profile": profile_data,
            "is_own": participant_id == p["id"],
            "profile_back_url": _ranking_return_url(
                token, return_view, return_department, return_members
            ),
        })
    return templates.TemplateResponse(request, "profile.html", {"request": request, **ctx})


async def _build_profile(participant_id: int, db, viewer_id: int = None) -> dict:
    """Build profile data for a participant."""
    row = await db.execute("SELECT * FROM participants WHERE id=?", (participant_id,))
    p = dict(await row.fetchone())
    display_name = _display_name(p)
    is_limited_view = bool(viewer_id and viewer_id != participant_id and p.get("profile_visibility") == "limited")
    # Total points + rank
    rankings = await get_rankings(db)
    rank_data = next((r for r in rankings if r["id"] == participant_id), None)
    total_points = rank_data["total_points"] if rank_data else 0
    user_rank = rank_data["rank"] if rank_data else "—"
    # Match count
    mc_row = await db.execute("SELECT COUNT(*) as cnt FROM predictions WHERE participant_id=?", (participant_id,))
    match_count = (await mc_row.fetchone())["cnt"]
    # Success rate
    played_row = await db.execute(
        """SELECT m.id AS match_id,
                  m.phase, m.score_team1, m.score_team2, m.result, m.qualifier_winner,
                  m.match_date, m.kickoff_time,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  pr.qualifier_prediction
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=? AND m.result IS NOT NULL""",
        (participant_id,)
    )
    played_predictions = [dict(r) for r in await played_row.fetchall()]
    total_played = len(played_predictions)
    correct = sum(1 for row in played_predictions if is_match_prediction_correct(row, row))
    exact = sum(1 for row in played_predictions if is_match_score_exact(row, row))
    success_rate = round(correct / total_played * 100) if total_played else 0
    # Séries (chronologique) : plus longue série historique + série en cours.
    streak_row = await db.execute(
        """SELECT m.phase, m.score_team1, m.score_team2, m.result, m.qualifier_winner,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  pr.qualifier_prediction
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=? AND m.result IS NOT NULL
           ORDER BY m.match_date ASC, m.kickoff_time ASC""",
        (participant_id,)
    )
    streak_preds = await streak_row.fetchall()
    longest_streak = 0
    run = 0
    for sp in streak_preds:
        sp_dict = dict(sp)
        if is_match_prediction_correct(sp_dict, sp_dict):
            run += 1
            longest_streak = max(longest_streak, run)
        else:
            run = 0
    streak = run  # série EN COURS = série en fin de liste chronologique
    # (Le trophée « La Série » est attribué depuis longest_streak par app.trophies.)

    # Near-miss : bon résultat mais score exact raté à un seul but près.
    near_miss = sum(
        1 for r in played_predictions
        if is_match_prediction_correct(r, r) and not is_match_score_exact(r, r)
        and r.get("exact_score_team1") is not None and r.get("score_team1") is not None
        and (abs(r["exact_score_team1"] - r["score_team1"])
             + abs(r["exact_score_team2"] - r["score_team2"])) == 1
    )
    # Journée parfaite : une journée sportive d'au moins 3 matchs avec résultat,
    # tous pronostiqués par le participant et tous corrects.
    result_rows = await db.execute(
        """SELECT id AS match_id, match_date, kickoff_time
           FROM matches
           WHERE result IS NOT NULL"""
    )
    by_sday = defaultdict(list)
    for r in await result_rows.fetchall():
        row = dict(r)
        by_sday[sporting_day(row)].append(row["match_id"])
    predictions_by_match = {r["match_id"]: r for r in played_predictions}
    perfect_day = any(
        len(day) >= 3
        and all(mid in predictions_by_match for mid in day)
        and all(is_match_prediction_correct(predictions_by_match[mid], predictions_by_match[mid]) for mid in day)
        for day in by_sday.values()
    )
    # Présent : plus longue série de JOURS de connexion d'affilée (cf.
    # _record_daily_visit). Indépendant des matchs/du calendrier et des pronos
    # groupés — on récompense l'habitude de revenir, pas la soumission.
    present_streak = p.get("best_visit_streak") or 0
    # Best day
    best_day_row = await db.execute(
        """SELECT m.group_name, SUM(s.points) as day_points
           FROM scores s
           JOIN matches m ON m.id = s.match_id
           WHERE s.participant_id=? AND m.group_name IS NOT NULL AND m.group_name != ''
           GROUP BY m.group_name
           ORDER BY day_points DESC LIMIT 1""",
        (participant_id,)
    )
    best_day_data = await best_day_row.fetchone()
    best_day = best_day_data["group_name"] if best_day_data else "—"
    best_day_pts = best_day_data["day_points"] if best_day_data else 0
    # Last 5 matches (for Phase 1 profile)
    last5_row = await db.execute(
        """SELECT m.team1_name, m.team2_name, m.phase,
                  m.score_team1, m.score_team2, m.result, m.qualifier_winner,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  pr.qualifier_prediction, COALESCE(s.points, 0) as points
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           LEFT JOIN scores s ON s.match_id = pr.match_id AND s.participant_id=pr.participant_id
           WHERE pr.participant_id=? AND m.result IS NOT NULL
           ORDER BY m.match_date DESC, m.kickoff_time DESC
           LIMIT 5""",
        (participant_id,)
    )
    last5 = [dict(r) for r in await last5_row.fetchall()]
    for m in last5:
        if is_match_score_exact(m, m):
            m["result_chip"] = "exact"
        elif is_match_prediction_correct(m, m):
            m["result_chip"] = "ok"
        else:
            m["result_chip"] = "miss"
    # Forme récente: 10 derniers matchs joués, du plus ancien au plus récent
    form_rows = await db.execute(
        """SELECT COALESCE(s.points, 0) AS points
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           LEFT JOIN scores s ON s.match_id = pr.match_id AND s.participant_id = pr.participant_id
           WHERE pr.participant_id=? AND m.result IS NOT NULL
           ORDER BY m.match_date DESC, m.kickoff_time DESC
           LIMIT 10""",
        (participant_id,)
    )
    recent_form = [dict(r) for r in await form_rows.fetchall()][::-1]
    for f in recent_form:
        f["cls"] = "hi" if f["points"] >= 4 else ("mid" if f["points"] > 0 else "")
        f["height"] = max(14, min(100, round(f["points"] / 6 * 100)))
    # Total de matchs avec résultat (pour le badge d'assiduité)
    tr_row = await db.execute("SELECT COUNT(*) AS cnt FROM matches WHERE result IS NOT NULL")
    total_results = (await tr_row.fetchone())["cnt"]
    # Soumissions de dernière minute (< 60 min avant le coup d'envoi)
    lm_row = await db.execute(
        """SELECT COUNT(*) AS cnt
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=?
             AND pr.submitted_at >= datetime(m.match_date || 'T' || m.kickoff_time, '-60 minutes')
             AND pr.submitted_at <= datetime(m.match_date || 'T' || m.kickoff_time)""",
        (participant_id,)
    )
    last_minute_count = (await lm_row.fetchone())["cnt"]
    # Délai moyen de soumission avant le coup d'envoi (heures)
    lead_row = await db.execute(
        """SELECT AVG((julianday(datetime(m.match_date || 'T' || m.kickoff_time))
                       - julianday(pr.submitted_at)) * 24) AS hours
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=?
             AND pr.submitted_at <= datetime(m.match_date || 'T' || m.kickoff_time)""",
        (participant_id,)
    )
    lead_hours_raw = (await lead_row.fetchone())["hours"]
    avg_lead_hours = round(lead_hours_raw, 1) if lead_hours_raw is not None else None
    # Équipe la plus souvent donnée gagnante (équipes réelles uniquement).
    # Matchs verrouillés seulement : ne pas révéler les pronos encore modifiables.
    fav_rows = await db.execute(
        """SELECT CASE WHEN pr.prediction='team1' THEN m.team1_name
                       ELSE m.team2_name END AS team, COUNT(*) AS cnt
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=? AND pr.prediction != 'draw'
             AND datetime(m.match_date || 'T' || m.kickoff_time) <= datetime(?)
           GROUP BY team ORDER BY cnt DESC LIMIT 8""",
        (participant_id, _now_utc())
    )
    favorite_pick = next(
        ((r["team"], r["cnt"]) for r in await fav_rows.fetchall() if team_flag(r["team"])),
        None,
    )
    # Nuls tentés / réussis (matchs verrouillés seulement, même raison)
    draw_row = await db.execute(
        """SELECT COUNT(*) AS cnt
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=? AND pr.prediction='draw'
             AND datetime(m.match_date || 'T' || m.kickoff_time) <= datetime(?)""",
        (participant_id, _now_utc())
    )
    draw_attempts = (await draw_row.fetchone())["cnt"]
    draw_correct = sum(
        1 for r in played_predictions
        if r["phase"] == "group" and r["prediction"] == "draw" and r["result"] == "draw"
    )
    # Roi des bonus: points bonus cumulés du participant
    bonus_rankings = await get_rankings(db, scope="bonus")
    bonus_points = next(
        (r["total_points"] for r in bonus_rankings if r["id"] == participant_id), 0
    )
    # Cabinet à trophées (W8) — archive permanente lue depuis trophy_awards.
    show_trophy_cabinet = not is_limited_view
    trophy_groups = []
    trophy_summary = {"unlocked_count": 0, "total": 0}
    latest_trophy = None
    just_unlocked = 0
    if show_trophy_cabinet:
        cabinet = await build_cabinet(db, participant_id)
        trophy_groups = cabinet["groups"]
        trophy_summary = {"unlocked_count": cabinet["unlocked_count"], "total": cabinet["total"]}
        latest_trophy = cabinet["latest"]

        # Célébration : trophées nouvellement débloqués depuis la dernière visite
        # du propriétaire. Première visite post-déploiement = init silencieuse.
        if viewer_id is None:
            raw_seen = p.get("seen_trophies")
            unlocked_keys = cabinet["unlocked_keys"]
            if raw_seen is None:
                await db.execute(
                    "UPDATE participants SET seen_trophies=? WHERE id=?",
                    (",".join(sorted(unlocked_keys)), participant_id),
                )
                await db.commit()
            else:
                seen = set(raw_seen.split(",")) - {""}
                new_keys = unlocked_keys - seen
                if new_keys:
                    for grp in trophy_groups:
                        for t in grp["items"]:
                            if t["key"] in new_keys:
                                t["just_unlocked"] = True
                    just_unlocked = len(new_keys)
                    seen |= unlocked_keys
                    await db.execute(
                        "UPDATE participants SET seen_trophies=? WHERE id=?",
                        (",".join(sorted(seen)), participant_id),
                    )
                    await db.commit()
    fun_stats = []
    if favorite_pick:
        fun_stats.append({"icon": "❤️", "label": "Équipe la plus jouée gagnante",
                          "value": f"{team_flag(favorite_pick[0])} {favorite_pick[0]} ({favorite_pick[1]}×)"})
    if draw_attempts:
        fun_stats.append({"icon": "🤝", "label": "Matchs nuls tentés / réussis",
                          "value": f"{draw_attempts} / {draw_correct}"})
    if avg_lead_hours is not None:
        if avg_lead_hours >= 48:
            lead_label = f"{round(avg_lead_hours / 24)} jours avant"
        elif avg_lead_hours >= 1:
            lead_label = f"{round(avg_lead_hours)} h avant"
        else:
            lead_label = f"{max(1, round(avg_lead_hours * 60))} min avant"
        fun_stats.append({"icon": "⏱️", "label": "Délai moyen de soumission",
                          "value": lead_label})
    # Comparison (if viewer is different)
    comparison = None
    if viewer_id and viewer_id != participant_id:
        viewer_rank = next((r for r in rankings if r["id"] == viewer_id), None)
        if viewer_rank:
            viewer_pts = viewer_rank["total_points"]
            diff = viewer_pts - total_points
            comparison = {"viewer_pts": viewer_pts, "target_pts": total_points, "diff": diff}
    color_class = f"c{((participant_id - 1) % 8) + 1}"
    initials = _initials(display_name)
    return {
        "participant": p,
        "name": display_name,
        "avatar_path": p.get("avatar_path"),
        "full_name": p["name"],
        "nickname": p.get("nickname"),
        "favorite_team": "" if is_limited_view else p.get("favorite_team"),
        "department": p.get("department") or "",
        "has_password": bool(p.get("password_hash")),
        "bio": "" if is_limited_view else p.get("bio"),
        "profile_visibility": p.get("profile_visibility") or "public",
        "email_opt_in": p.get("email_opt_in"),
        "is_limited_view": is_limited_view,
        "initials": initials,
        "color_class": color_class,
        "rank": user_rank,
        "total_points": total_points,
        "match_count": match_count,
        "total_played": total_played,
        "success_rate": success_rate,
        "exact_count": exact,
        "streak": streak,
        "longest_streak": longest_streak,
        "best_day": best_day,
        "best_day_pts": best_day_pts,
        "last5": [] if is_limited_view else last5,
        "recent_form": [] if is_limited_view else recent_form,
        "trophy_groups": trophy_groups,
        "trophy_summary": trophy_summary,
        "latest_trophy": latest_trophy,
        "trophies_just_unlocked": just_unlocked,
        "show_trophy_cabinet": show_trophy_cabinet,
        "fun_stats": [] if is_limited_view else fun_stats,
        "comparison": comparison,
    }


@router.get("/p/{token}/bonus", response_class=HTMLResponse)
async def bonus_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "bonus")
        p = ctx["participant"]
        now = _now_utc()
        rows = await db.execute(
            """SELECT bq.*,
                      ba.answer,
                      ba.submitted_at as answered_at,
                      s.points,
                      s.calculated_at as scored_at
               FROM bonus_questions bq
               LEFT JOIN bonus_answers ba
                 ON ba.question_id = bq.id AND ba.participant_id = ?
               LEFT JOIN scores s
                 ON s.bonus_question_id = bq.id AND s.participant_id = ?
               WHERE bq.is_published=1
               ORDER BY
                 CASE WHEN bq.deadline > ? THEN 0 ELSE 1 END,
                 bq.deadline ASC,
                 bq.id ASC""",
            (p["id"], p["id"], now)
        )
        bonus_questions = []
        pending_count = 0
        for row in await rows.fetchall():
            q = dict(row)
            q["is_open"] = q["deadline"] > now
            q["has_answer"] = q["answer"] is not None
            q["has_score"] = q["points"] is not None
            q["can_edit"] = q["is_open"] and not q["has_score"]
            if q["answer_type"] == "multi_choice":
                q["answer_set"] = parse_team_set(q["answer"])
                q["answer_display"] = format_team_list(q["answer"])
                q["correct_answer_display"] = format_team_list(q["correct_answer"])
            elif q["answer_type"] == "number_multi":
                parsed = parse_number_multi(q["answer"])
                q["answer_set"] = parsed["teams"]
                q["answer_count"] = parsed["count"]
                q["answer_display"] = format_number_multi(q["answer"])
                q["correct_answer_display"] = format_number_multi(q["correct_answer"])
            else:
                q["answer_set"] = set()
                q["answer_count"] = None
                q["answer_display"] = q["answer"]
                q["correct_answer_display"] = q["correct_answer"]
            if q["is_open"] and not q["has_answer"]:
                pending_count += 1
            bonus_questions.append(q)
        pt_score_row = await db.execute(
            "SELECT COALESCE(SUM(points), 0) as total FROM pre_tournament_scores WHERE participant_id=?",
            (p["id"],),
        )
        pt_points = (await pt_score_row.fetchone())["total"]
        pt_scored_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM pre_tournament_scores WHERE participant_id=?",
            (p["id"],),
        )
        pt_scored = (await pt_scored_row.fetchone())["cnt"] > 0
        ctx.update({
            "bonus_questions": bonus_questions,
            "pending_bonus_questions": pending_count,
            "now": now,
            "phase_labels": {"pre_tournament": "Pré-tournoi", **PHASE_LABELS},
            "pt_points": pt_points,
            "pt_scored": pt_scored,
        })
    return templates.TemplateResponse(request, "bonus.html", {"request": request, **ctx})


@router.post("/p/{token}/bonus/{question_id}", response_class=HTMLResponse)
async def submit_bonus(request: Request, token: str, question_id: int):
    p = await require_participant(token)
    form = await request.form()
    async with get_db() as db:
        q_row = await db.execute(
            "SELECT * FROM bonus_questions WHERE id=? AND is_published=1",
            (question_id,),
        )
        q = await q_row.fetchone()
        if not q:
            raise HTTPException(404)
        if q["deadline"] < _now_utc():
            raise HTTPException(403, "Deadline dépassée")
        if q["answer_type"] == "multi_choice":
            options = json.loads(q["options"]) if q["options"] else []
            selected = [v for v in form.getlist("answer") if v in options]
            if not selected:
                raise HTTPException(400, "Coche au moins une équipe")
            answer = json.dumps(selected, ensure_ascii=False)
        elif q["answer_type"] == "number_multi":
            options = json.loads(q["options"]) if q["options"] else []
            raw_count = (form.get("count") or "").strip()
            try:
                count = int(raw_count)
            except ValueError:
                raise HTTPException(400, "Total invalide")
            if count < 1 or count > 8:
                raise HTTPException(400, "Total hors limites (1 à 8)")
            teams = [v for v in form.getlist("teams") if v in options]
            if len(set(teams)) != count - 1:
                raise HTTPException(400, f"Sélectionne exactement {count - 1} équipe(s)")
            answer = json.dumps({"count": count, "teams": teams}, ensure_ascii=False)
        else:
            answer = (form.get("answer") or "").strip()
            if not answer:
                raise HTTPException(400, "Réponse vide")
            if q["answer_type"] == "choice" and q["options"]:
                options = json.loads(q["options"])
                if answer not in options:
                    raise HTTPException(400, "Réponse invalide")
            if q["answer_type"] == "number" and parse_bonus_number(answer) is None:
                raise HTTPException(400, "Réponse numérique invalide")
        await db.execute(
            """INSERT INTO bonus_answers (participant_id, question_id, answer)
               VALUES (?,?,?)
               ON CONFLICT(participant_id, question_id) DO UPDATE SET answer=excluded.answer""",
            (p["id"], question_id, answer)
        )
        await db.commit()
    return RedirectResponse(url=f"/p/{token}/bonus", status_code=303)
