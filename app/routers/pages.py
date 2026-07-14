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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

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
from app.database import (
    ROUND16_FASTEST_GOAL_TEXT,
    ROUND32_FAVORITE_CONCEDE_TEXT,
    ROUND32_TIEBREAK_COUNT_TEXT,
    get_db,
)
from app.easter_egg import apply_balogun_swap, get_deactivation_match, is_balogun_easter_egg_active
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
    _scoring_points_by_day,
    actual_match_winner,
    bonus_number_is_integer,
    compose_minute_notation,
    format_minute_notation,
    format_number_multi,
    format_team_list,
    get_department_rankings,
    get_latest_finalized_climbers,
    get_rank_evolution,
    get_rankings,
    get_remontada,
    get_sporting_day_states,
    is_match_prediction_correct,
    is_match_score_exact,
    normalize_closest_config,
    normalize_multi_select_config,
    normalize_number_multi_config,
    parse_bonus_number,
    parse_number_multi,
    parse_team_set,
    predicted_match_winner,
    split_minute_notation,
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
    sporting_day_for_timestamp,
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

# Archives du hub /bonus : étapes du plus récent au plus ancien.
BONUS_HUB_PHASE_ORDER = [
    "final",
    "third_place",
    "semi",
    "quarter",
    "round_of_16",
    "round_of_32",
    "group",
    "pre_tournament",
]

PRE_TOURNAMENT_BONUS_CARD_LABELS = {
    "winner": "Champion",
    "finalist": "Finalistes",
    "top_scorer": "Buteur",
    "revelation": "Révélation",
    "total_goals": "Buts",
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


def _points_tone(points: int) -> str:
    if points > 0:
        return "pos"
    if points < 0:
        return "neg"
    return "zero"


def _prediction_score_display(match: dict) -> str:
    s1, s2 = match.get("exact_score_team1"), match.get("exact_score_team2")
    if s1 is None or s2 is None:
        return _prediction_label(match)
    label = f"{s1}-{s2}"
    if match.get("phase") != "group":
        qualifier = match.get("qualifier_prediction")
        if qualifier not in ("team1", "team2"):
            qualifier = "team1" if s1 > s2 else "team2" if s2 > s1 else None
        if qualifier in ("team1", "team2"):
            team = match["team1_name"] if qualifier == "team1" else match["team2_name"]
            label = f"{label}, {team} qualifiée"
    return label


def _point_journal_match_detail(match: dict) -> str:
    parts = [f"Prono {_prediction_score_display(match)}"]
    result = result_full_label(match)
    if result:
        parts.append(f"résultat {result}")
    tier = _prediction_tier(match, match)
    if tier == "exact":
        parts.append("score exact")
    elif tier == "correct":
        parts.append("bon résultat")
    else:
        parts.append(_zero_points_reason(match, match).lower())
    return " · ".join(parts)


async def _build_point_journal(db, participant_id: int, token: str, limit: int = 10) -> list[dict]:
    """Derniers mouvements lisibles qui expliquent le total du participant.

    Matchs : valeur courante. Bonus/pré-tournoi : événements de delta quand ils
    existent, avec fallback sur la valeur courante pour les 0 pt ou données
    historiques sans event.
    """
    items: list[dict] = []

    match_rows = await db.execute(
        """SELECT m.id AS match_id, m.team1_name, m.team2_name, m.phase,
                  m.match_date, m.kickoff_time,
                  m.score_team1, m.score_team2, m.final_score_team1, m.final_score_team2,
                  m.result, m.qualifier_winner,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  pr.qualifier_prediction,
                  COALESCE(s.points, 0) AS points
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           LEFT JOIN scores s ON s.match_id = pr.match_id AND s.participant_id = pr.participant_id
           WHERE pr.participant_id=? AND m.result IS NOT NULL
           ORDER BY m.match_date DESC, m.kickoff_time DESC
           LIMIT ?""",
        (participant_id, limit * 2),
    )
    for row in await match_rows.fetchall():
        match = dict(row)
        points = int(match.get("points") or 0)
        items.append({
            "type": "match",
            "title": f"{match['team1_name']} - {match['team2_name']}",
            "detail": _point_journal_match_detail(match),
            "points": points,
            "occurred_at": f"{match['match_date']}T{match['kickoff_time']}",
            "href": f"/p/{token}/match/{match['match_id']}",
            "tone": _points_tone(points),
        })

    event_rows = await db.execute(
        """SELECT id, source, source_key, delta, occurred_at
           FROM scoring_point_events
           WHERE participant_id=?
           ORDER BY datetime(occurred_at) DESC, id DESC""",
        (participant_id,),
    )
    events = [dict(r) for r in await event_rows.fetchall()]
    event_keys = {(r["source"], str(r["source_key"])) for r in events}

    bonus_ids = sorted({str(r["source_key"]) for r in events if r["source"] == "bonus"})
    pt_keys = sorted({str(r["source_key"]) for r in events if r["source"] == "pre_tournament"})
    bonus_titles: dict[str, str] = {}
    pt_titles: dict[str, str] = {}
    if bonus_ids:
        placeholders = ",".join("?" for _ in bonus_ids)
        rows = await db.execute(
            f"SELECT id, question_text FROM bonus_questions WHERE id IN ({placeholders})",
            [int(x) for x in bonus_ids],
        )
        for r in await rows.fetchall():
            title, prompt = _bonus_title_prompt(r["question_text"])
            bonus_titles[str(r["id"])] = title or prompt or "Question bonus"
    if pt_keys:
        placeholders = ",".join("?" for _ in pt_keys)
        rows = await db.execute(
            f"SELECT key, label FROM pre_tournament_questions WHERE key IN ({placeholders})",
            pt_keys,
        )
        pt_titles = {r["key"]: r["label"] for r in await rows.fetchall()}

    for event in events:
        source = event["source"]
        key = str(event["source_key"])
        points = int(event["delta"] or 0)
        if source == "bonus":
            title = bonus_titles.get(key, "Question bonus")
            detail = "Correction bonus" if points < 0 else "Résultat bonus"
            href = f"/p/{token}/bonus#bonus-q-{key}"
        else:
            title = pt_titles.get(key, "Question pré-tournoi")
            detail = "Correction pré-tournoi" if points < 0 else "Résultat pré-tournoi"
            href = f"/p/{token}/pre-tournoi#pt-{key}"
        items.append({
            "type": source,
            "title": title,
            "detail": detail,
            "points": points,
            "occurred_at": str(event["occurred_at"]).replace(" ", "T"),
            "href": href,
            "tone": _points_tone(points),
        })

    fallback_rows = await db.execute(
        """SELECT bq.id, bq.question_text, s.points, s.calculated_at
           FROM scores s
           JOIN bonus_questions bq ON bq.id = s.bonus_question_id
           WHERE s.participant_id=? AND s.bonus_question_id IS NOT NULL""",
        (participant_id,),
    )
    for row in await fallback_rows.fetchall():
        r = dict(row)
        key = str(r["id"])
        if ("bonus", key) in event_keys:
            continue
        title, prompt = _bonus_title_prompt(r["question_text"])
        points = int(r["points"] or 0)
        items.append({
            "type": "bonus",
            "title": title or prompt or "Question bonus",
            "detail": "Résultat bonus",
            "points": points,
            "occurred_at": str(r["calculated_at"]).replace(" ", "T"),
            "href": f"/p/{token}/bonus#bonus-q-{key}",
            "tone": _points_tone(points),
        })

    pt_fallback_rows = await db.execute(
        """SELECT ps.question_key, pq.label, ps.points, ps.calculated_at
           FROM pre_tournament_scores ps
           JOIN pre_tournament_questions pq ON pq.key = ps.question_key
           WHERE ps.participant_id=?""",
        (participant_id,),
    )
    for row in await pt_fallback_rows.fetchall():
        r = dict(row)
        key = str(r["question_key"])
        if ("pre_tournament", key) in event_keys:
            continue
        points = int(r["points"] or 0)
        items.append({
            "type": "pre_tournament",
            "title": r["label"],
            "detail": "Résultat pré-tournoi",
            "points": points,
            "occurred_at": str(r["calculated_at"]).replace(" ", "T"),
            "href": f"/p/{token}/pre-tournoi#pt-{key}",
            "tone": _points_tone(points),
        })

    items.sort(key=lambda item: item["occurred_at"], reverse=True)
    return items[:limit]


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
        "phase_labels": {"pre_tournament": "Pré-tournoi", **PHASE_LABELS},
        "page_wide": True,
        "token": token,
        "client_now_ts": int(now_utc().timestamp()),
        "news_story": news_story,
    }


async def _reveal_rank_evolution(db, participant_id: int, selected_days: list,
                                 through_day: str | None = None) -> dict:
    """Évolution de rang provoquée par la fenêtre (matchs + bonus + pré-tournoi),
    focus participant.

    Avant = classement actuel privé des points gagnés sur ces jours (déterministe,
    sans état stocké, `_scoring_points_by_day` unifie matchs/bonus/pré-tournoi —
    cf. `app/scoring.py`). Renvoie les extraits 3 lignes (dessus/toi/dessous)
    avant/après.
    """
    current = await get_rankings(db)
    if not current or not selected_days:
        return {}
    points_by_day = await _scoring_points_by_day(db)
    selected = set(selected_days)
    win_pts: dict[int, int] = {}
    for day in selected:
        for pid, pts in points_by_day.get(day, {}).items():
            win_pts[pid] = win_pts.get(pid, 0) + pts
    after_points = {r["id"]: r["total_points"] for r in current}
    if through_day:
        for day, day_pts in points_by_day.items():
            if day > through_day:
                for pid, pts in day_pts.items():
                    if pid in after_points:
                        after_points[pid] -= pts
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


async def _bonus_pretournament_window_items(
    db, participant_id: int, selected_days: list
) -> tuple[list[dict], list[dict]]:
    """Cartes bonus/pré-tournoi de la fenêtre, sourcées de `scoring_point_events`
    (le delta réel de l'événement, pas la valeur absolue courante de la question
    — cf. la note "delta vs valeur absolue" du plan de correction : après une
    correction rétroactive, la valeur courante ne représente plus le delta du
    jour où l'événement historique a eu lieu). Une carte par question touchée
    dans la fenêtre, même si le delta du participant est nul (symétrie avec les
    cartes de match, toujours affichées même à 0 pt / sans prono)."""
    selected = set(selected_days)
    rows = await db.execute(
        "SELECT source, source_key, participant_id, delta, occurred_at FROM scoring_point_events"
    )
    by_source_key: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
    for r in await rows.fetchall():
        day = sporting_day_for_timestamp(r["occurred_at"])
        if day not in selected:
            continue
        bucket = by_source_key[(r["source"], r["source_key"])]
        bucket[r["participant_id"]] = bucket.get(r["participant_id"], 0) + r["delta"]

    bonus_items: list[dict] = []
    pretournament_items: list[dict] = []
    if not by_source_key:
        return bonus_items, pretournament_items

    bonus_ids = [key for (src, key) in by_source_key if src == "bonus"]
    pt_keys = [key for (src, key) in by_source_key if src == "pre_tournament"]

    bonus_titles: dict[str, str] = {}
    if bonus_ids:
        placeholders = ",".join("?" for _ in bonus_ids)
        qrows = await db.execute(
            f"SELECT id, question_text FROM bonus_questions WHERE id IN ({placeholders})",
            [int(x) for x in bonus_ids],
        )
        for r in await qrows.fetchall():
            title, prompt = _bonus_title_prompt(r["question_text"])
            bonus_titles[str(r["id"])] = title or prompt

    pt_titles: dict[str, str] = {}
    if pt_keys:
        placeholders = ",".join("?" for _ in pt_keys)
        qrows = await db.execute(
            f"SELECT key, label FROM pre_tournament_questions WHERE key IN ({placeholders})",
            pt_keys,
        )
        pt_titles = {r["key"]: r["label"] for r in await qrows.fetchall()}

    for (source, key), deltas in by_source_key.items():
        points = deltas.get(participant_id, 0)
        if source == "bonus":
            bonus_items.append({
                "question_id": key,
                "title": bonus_titles.get(key, ""),
                "points": points,
            })
        else:
            pretournament_items.append({
                "question_key": key,
                "title": pt_titles.get(key, ""),
                "points": points,
            })
    bonus_items.sort(key=lambda i: i["title"])
    pretournament_items.sort(key=lambda i: i["title"])
    return bonus_items, pretournament_items


async def _reveal_window_data(db, participant_id: int) -> dict | None:
    """Journées finalisées depuis la connexion précédente, dernière garantie.

    Une journée peut désormais être "finalisée" par une activité bonus/pré-tournoi
    seule (aucun match ce jour) — cf. `get_sporting_day_states`.
    """
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

    states = await get_sporting_day_states(db)
    finalized_days = sorted(day for day, state in states.items() if state["finalized"])
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
    extra = {}
    if window_ids:
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
    bonus_items, pretournament_items = await _bonus_pretournament_window_items(
        db, participant_id, selected_days
    )
    bonus_points = sum(item["points"] for item in bonus_items)
    pretournament_points = sum(item["points"] for item in pretournament_items)

    reveal_sd = selected_days[-1]
    is_catchup = len(selected_days) > 1
    return {
        "sporting_day": reveal_sd,
        "day_label": "Depuis ta dernière visite" if is_catchup else format_sporting_day_fr(reveal_sd),
        "days": day_groups,
        "day_count": len(day_groups),
        "is_catchup": is_catchup,
        "matches": matches,
        "bonus_items": bonus_items,
        "pretournament_items": pretournament_items,
        "total_points": total + bonus_points + pretournament_points,
        "exact_count": exact_count,
        "match_count": len(matches),
        "evolution": await _reveal_rank_evolution(
            db, participant_id, selected_days, through_day=reveal_sd
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
        if ctx["pending_bonus"] > 0:
            home_bonus_questions, pending_bonus_count = await _load_bonus_questions(
                db,
                p["id"],
                _now_utc(),
                only_pending=True,
            )
            ctx["home_bonus_questions"] = home_bonus_questions
            ctx["pending_bonus"] = pending_bonus_count
        else:
            ctx["home_bonus_questions"] = []
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
        ctx["reveal_bonus_count"] = (
            len(reveal["bonus_items"]) + len(reveal["pretournament_items"]) if reveal else 0
        )
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
        # Results are visible only after the pre-tournament deadline.
        score_rows = await db.execute(
            "SELECT question_key, points FROM pre_tournament_scores WHERE participant_id=?",
            (p["id"],),
        )
        raw_pt_scores = {
            r["question_key"]: r["points"] for r in await score_rows.fetchall()
        }
        pt_scores = {} if pt_status["open"] else raw_pt_scores
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
    "bonus": "Bonus",
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


BONUS_RANKING_PHASE_ORDER = list(reversed(BONUS_HUB_PHASE_ORDER))


async def _load_bonus_ranking_breakdowns(
    db, participant_ids, now: str
) -> dict[int, dict]:
    """Explain every participant's Bonus total with two batch score reads.

    Every globally resolved, published question is included for every ranked
    participant. Missing answers therefore appear explicitly as 0 pt instead
    of disappearing from the explanation.
    """
    participant_ids = list(participant_ids)
    if not participant_ids:
        return {}

    question_rows = await db.execute(
        """SELECT bq.id, bq.phase, bq.question_text, bq.deadline
           FROM bonus_questions bq
           WHERE bq.is_published = 1
             AND datetime(bq.deadline) <= datetime(?)
             AND (
                 bq.correct_answer IS NOT NULL
                 OR EXISTS (
                     SELECT 1 FROM scores resolved_score
                     WHERE resolved_score.bonus_question_id = bq.id
                 )
             )
           ORDER BY bq.deadline, bq.id""",
        (now,),
    )
    classic_questions = []
    for row in await question_rows.fetchall():
        question = dict(row)
        title, prompt = _bonus_title_prompt(question["question_text"])
        classic_questions.append({
            "source": "bonus",
            "key": question["id"],
            "phase": question["phase"],
            "label": title or prompt,
            "description": prompt if title else "",
            "sort_key": (1, question["deadline"], question["id"]),
        })

    pt_questions = []
    pt_deadline = await get_pre_tournament_deadline(db)
    if pt_deadline <= now:
        pt_rows = await db.execute(
            """SELECT ptq.key, ptq.label, ptq.sort_order
               FROM pre_tournament_questions ptq
               WHERE ptq.is_enabled = 1
                 AND (
                     ptq.correct_answer IS NOT NULL
                     OR EXISTS (
                         SELECT 1 FROM pre_tournament_scores resolved_score
                         WHERE resolved_score.question_key = ptq.key
                     )
                 )
               ORDER BY ptq.sort_order, ptq.key"""
        )
        pt_questions = [
            {
                "source": "pre_tournament",
                "key": row["key"],
                "phase": "pre_tournament",
                "label": row["label"],
                "description": "",
                "sort_key": (0, row["sort_order"], row["key"]),
            }
            for row in await pt_rows.fetchall()
        ]

    questions = classic_questions + pt_questions
    points_by_item = {}
    participant_marks = ",".join("?" for _ in participant_ids)
    classic_ids = [question["key"] for question in classic_questions]
    if classic_ids:
        question_marks = ",".join("?" for _ in classic_ids)
        score_rows = await db.execute(
            f"""SELECT s.participant_id, s.bonus_question_id, s.points
                FROM scores s
                WHERE s.participant_id IN ({participant_marks})
                  AND s.bonus_question_id IN ({question_marks})
                  AND s.id = (
                      SELECT MAX(latest.id)
                      FROM scores latest
                      WHERE latest.participant_id = s.participant_id
                        AND latest.bonus_question_id = s.bonus_question_id
                  )""",
            (*participant_ids, *classic_ids),
        )
        for row in await score_rows.fetchall():
            points_by_item[(row["participant_id"], "bonus", row["bonus_question_id"])] = (
                row["points"] or 0
            )

    pt_keys = [question["key"] for question in pt_questions]
    if pt_keys:
        key_marks = ",".join("?" for _ in pt_keys)
        score_rows = await db.execute(
            f"""SELECT participant_id, question_key, points
                FROM pre_tournament_scores
                WHERE participant_id IN ({participant_marks})
                  AND question_key IN ({key_marks})""",
            (*participant_ids, *pt_keys),
        )
        for row in await score_rows.fetchall():
            points_by_item[(row["participant_id"], "pre_tournament", row["question_key"])] = (
                row["points"] or 0
            )

    phase_rank = {
        phase: index for index, phase in enumerate(BONUS_RANKING_PHASE_ORDER)
    }
    breakdowns = {}
    for participant_id in participant_ids:
        by_phase = {}
        for question in questions:
            points = points_by_item.get(
                (participant_id, question["source"], question["key"]), 0
            )
            by_phase.setdefault(question["phase"], []).append({
                "source": question["source"],
                "key": question["key"],
                "label": question["label"],
                "description": question["description"],
                "points": points,
                "sort_key": question["sort_key"],
            })
        phases = []
        for phase in sorted(
            by_phase,
            key=lambda key: phase_rank.get(key, len(phase_rank)),
        ):
            entries = sorted(by_phase[phase], key=lambda item: item["sort_key"])
            for entry in entries:
                entry.pop("sort_key", None)
            phases.append({
                "phase": phase,
                "label": (
                    "Pré-tournoi"
                    if phase == "pre_tournament"
                    else PHASE_LABELS.get(phase, phase)
                ),
                "points": sum(entry["points"] for entry in entries),
                "questions": entries,
            })
        breakdowns[participant_id] = {
            "total_points": sum(phase["points"] for phase in phases),
            "phases": phases,
        }
    return breakdowns


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
        bonus_breakdowns = {}
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
            if view == "bonus":
                bonus_breakdowns = await _load_bonus_ranking_breakdowns(
                    db, (row["id"] for row in rankings), _now_utc()
                )
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
            r["bonus_breakdown"] = bonus_breakdowns.get(
                r["id"], {"total_points": 0, "phases": []}
            )
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
        balogun_active = is_balogun_easter_egg_active(now_utc(), await get_deactivation_match(db))
        if balogun_active:
            apply_balogun_swap(ctx)
        ctx["balogun_easter_egg_active"] = balogun_active
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
        profile_data["point_journal"] = await _build_point_journal(db, p["id"], token)
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


def _bonus_title_prompt(question_text: str) -> tuple[str, str]:
    if " — " not in question_text:
        return "", question_text
    title, prompt = question_text.split(" — ", 1)
    return title.strip(), prompt.strip()


def _locked_teams_phrase(q: dict) -> str:
    """« Maroc », « Maroc et Égypte », « Maroc, Égypte et Ghana »… à partir des
    équipes verrouillées de la question (déjà qualifiées). Vide si aucune."""
    config = q.get("number_multi_config") or {}
    teams = [str(t).strip() for t in config.get("locked_teams") or [] if str(t).strip()]
    if not teams:
        return ""
    if len(teams) == 1:
        return teams[0]
    return f"{', '.join(teams[:-1])} et {teams[-1]}"


def _bonus_help_lead(q: dict, closest_config: dict | None = None) -> str:
    text = q.get("question_text") or ""
    if q.get("answer_type") == "number_multi":
        locked = _locked_teams_phrase(q)
        if locked:
            return f"{locked} compte déjà. Le total doit égaler les tuiles cochées."
        return "Le total doit égaler le nombre de tuiles cochées."
    if text == ROUND32_TIEBREAK_COUNT_TEXT:
        return f"Nombre exact uniquement : {q.get('points_value', 5)} pts, sinon 0."
    if text == ROUND32_FAVORITE_CONCEDE_TEXT:
        return f"Réponds de 0 à 6. Nombre exact : {q.get('points_value', 5)} pts."
    if q.get("scoring_mode") == "closest_podium" and closest_config:
        points = " / ".join(str(p) for p in closest_config["rank_points"] if p > 0)
        return f"Le plus proche marque {points} pts."
    return ""


def _bonus_answer_placeholder(q: dict) -> str:
    text = q.get("question_text") or ""
    if q.get("answer_type") == "number_multi":
        config = q.get("number_multi_config") or {}
        locked = _locked_teams_phrase(q)
        suffix = f" ({locked} inclus)" if locked else ""
        return f"Total : {config.get('min_count', 1)} à {config.get('max_count', 8)}{suffix}"
    if "Combien de nouvelles séances de tirs au but" in text:
        return "0, 1, 2..."
    if "Le Favori Qui Tremble" in text:
        return "0 à 6"
    if text == ROUND16_FASTEST_GOAL_TEXT:
        return "1 à 120"
    return "Ta réponse..."


async def _load_bonus_questions(db, participant_id: int, now: str, *, only_pending: bool = False):
    rows = await db.execute(
        """SELECT bq.*,
                  ba.answer,
                  ba.submitted_at as answered_at,
                  s.points,
                  s.calculated_at as scored_at,
                  (SELECT MAX(result_score.calculated_at)
                   FROM scores result_score
                   WHERE result_score.bonus_question_id = bq.id) as result_version
           FROM bonus_questions bq
           LEFT JOIN bonus_answers ba
             ON ba.question_id = bq.id AND ba.participant_id = ?
           LEFT JOIN scores s
             ON s.bonus_question_id = bq.id AND s.participant_id = ?
            AND s.id = (
                SELECT MAX(latest.id)
                FROM scores latest
                WHERE latest.bonus_question_id = bq.id
                  AND latest.participant_id = ?
            )
           WHERE bq.is_published=1
           ORDER BY
             CASE WHEN bq.deadline > ? THEN 0 ELSE 1 END,
             bq.deadline ASC,
             bq.id ASC""",
        (participant_id, participant_id, participant_id, now),
    )
    bonus_questions = []
    pending_count = 0
    for row in await rows.fetchall():
        q = dict(row)
        q["is_open"] = q["deadline"] > now
        q["has_answer"] = q["answer"] is not None
        q["has_score"] = q["points"] is not None
        q["is_resolved"] = (
            not q["is_open"]
            and (q["correct_answer"] is not None or q["result_version"] is not None)
        )
        q["can_edit"] = q["is_open"] and not q["has_score"]
        q["question_title"], q["question_prompt"] = _bonus_title_prompt(q["question_text"])
        q["answer_min"] = None
        q["answer_max"] = None
        q["integer_only"] = False
        q["minute_notation"] = False
        q["answer_minute"] = None
        q["answer_added"] = None
        q["number_multi_config"] = None
        closest_config = None
        try:
            opts = json.loads(q["options"]) if q.get("options") else []
        except Exception:
            opts = []
        if q["answer_type"] == "multi_choice":
            multi_select_config = normalize_multi_select_config(
                q.get("scoring_config")
            )
            q["answer_set"] = parse_team_set(q["answer"])
            q["answer_display"] = format_team_list(q["answer"])
            q["correct_answer_display"] = format_team_list(q["correct_answer"])
            q["all_or_nothing_options"] = multi_select_config[
                "all_or_nothing_options"
            ]
            q["locked_teams"] = set()
            q["points_label"] = f"{q['points_value']} pts"
        elif q["answer_type"] == "number_multi":
            parsed = parse_number_multi(q["answer"])
            number_config = normalize_number_multi_config(
                q["points_value"],
                q.get("scoring_config"),
                opts,
            )
            q["answer_set"] = parsed["teams"]
            q["answer_count"] = parsed["count"]
            q["answer_display"] = format_number_multi(q["answer"], opts)
            q["correct_answer_display"] = format_number_multi(q["correct_answer"], opts)
            q["number_multi_config"] = number_config
            q["locked_teams"] = set(number_config["locked_teams"])
            q["points_label"] = f"{number_config['max_points']} pts max"
        else:
            q["answer_set"] = set()
            q["answer_count"] = None
            q["answer_display"] = q["answer"]
            q["correct_answer_display"] = q["correct_answer"]
            q["locked_teams"] = set()
            if q["scoring_mode"] == "closest_podium":
                closest_config = normalize_closest_config(
                    q["points_value"],
                    q.get("scoring_config"),
                )
                visible_points = [p for p in closest_config["rank_points"] if p > 0]
                q["points_label"] = f"{' / '.join(str(p) for p in visible_points)} pts"
                q["answer_min"] = closest_config.get("min_value")
                q["answer_max"] = closest_config.get("max_value")
                q["integer_only"] = closest_config.get("integer_only", False)
                q["minute_notation"] = closest_config.get("minute_notation", False)
                if q["minute_notation"]:
                    if q["answer"]:
                        q["answer_display"] = format_minute_notation(q["answer"])
                    if q["correct_answer"]:
                        q["correct_answer_display"] = format_minute_notation(q["correct_answer"])
                    q["answer_minute"], q["answer_added"] = split_minute_notation(q["answer"])
            else:
                q["points_label"] = f"{q['points_value']} pts"
        q["help_lead"] = _bonus_help_lead(q, closest_config)
        q["answer_placeholder"] = _bonus_answer_placeholder(q)
        if q["is_open"] and not q["has_answer"]:
            pending_count += 1
        if only_pending and (not q["is_open"] or q["has_answer"]):
            continue
        bonus_questions.append(q)
    return bonus_questions, pending_count


def _bonus_options_list(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


BONUS_TREND_NAME_THRESHOLD = 2
BONUS_TREND_PREVIEW_SIZE = 3
BONUS_TREND_EXAMPLE_NAMES = 4


def _select_example_names(names, has_me, limit):
    """Jusqu'à `limit` noms d'exemple, garantissant que "moi" y figure si
    mon groupe est représenté (quitte à remplacer le dernier exemple
    non-moi). Retourne aussi les noms restants du groupe (pour le <details>
    local "Voir les X autres")."""
    selected = list(names[:limit])
    if has_me and not any(n["is_me"] for n in selected):
        me_entry = next(n for n in names if n["is_me"])
        if selected:
            selected[-1] = me_entry
        else:
            selected = [me_entry]
    selected_ids = {n["participant_id"] for n in selected}
    remaining = [n for n in names if n["participant_id"] not in selected_ids]
    return selected, remaining


def _build_peer_trend(groups: dict, sort_key) -> dict:
    """Transforme un dict de groupes bruts {clé: {display, names, has_me}} en
    structure d'affichage top 3 + mon groupe (preview) / reste (remaining).

    Partagé par les tendances des questions bonus classiques (B7a/B7a.1) et
    des cartes pré-tournoi (B7b) : même seuil d'anonymat, mêmes règles de
    préview et de <details> local/global.
    """
    ordered = sorted(groups.values(), key=sort_key)
    total = sum(len(g["names"]) for g in ordered)
    enriched = []
    my_index = None
    for idx, g in enumerate(ordered):
        count = len(g["names"])
        percentage = round(count * 100 / total) if total else 0
        if count < BONUS_TREND_NAME_THRESHOLD and not g["has_me"]:
            example_names, remaining_names = [], []
        else:
            example_names, remaining_names = _select_example_names(
                g["names"], g["has_me"], BONUS_TREND_EXAMPLE_NAMES
            )
        enriched.append({
            "display": g["display"],
            "names": g["names"],
            "has_me": g["has_me"],
            "count": count,
            "percentage": percentage,
            "example_names": example_names,
            "remaining_names": remaining_names,
            "show_names": count >= BONUS_TREND_NAME_THRESHOLD or g["has_me"],
        })
        if g["has_me"]:
            my_index = idx
    preview = enriched[:BONUS_TREND_PREVIEW_SIZE]
    preview_indices = set(range(len(preview)))
    if my_index is not None and my_index >= BONUS_TREND_PREVIEW_SIZE:
        preview = preview + [enriched[my_index]]
        preview_indices.add(my_index)
    remaining = [g for idx, g in enumerate(enriched) if idx not in preview_indices]
    return {"preview": preview, "remaining": remaining}


async def _load_bonus_peer_answers(db, questions, participant_id: int):
    """Tendances des réponses de tous les participants pour chaque question verrouillée.

    Ne requête que les questions verrouillées : une question ouverte ne peut
    jamais voir ses réponses tierces chargées ni exposées au template.
    """
    locked = {
        q["id"]: q
        for q in questions
        if not q["is_open"] and not q["is_resolved"]
    }
    if not locked:
        return {}
    placeholders = ",".join("?" for _ in locked)
    rows = await db.execute(
        f"""SELECT ba.question_id, ba.participant_id,
                   COALESCE(NULLIF(p.nickname, ''), p.name) as name,
                   ba.answer
            FROM bonus_answers ba
            JOIN participants p ON p.id = ba.participant_id
            WHERE ba.question_id IN ({placeholders})
              AND p.is_confirmed = 1 AND p.is_admin = 0
            ORDER BY name COLLATE NOCASE ASC""",
        tuple(locked),
    )
    by_question = {qid: {} for qid in locked}
    for row in await rows.fetchall():
        q = locked[row["question_id"]]
        if q["answer_type"] == "multi_choice":
            display = format_team_list(row["answer"])
            key = display
        elif q["answer_type"] == "number_multi":
            display = format_number_multi(row["answer"], _bonus_options_list(q["options"]))
            key = display
        elif q["answer_type"] == "number":
            # "3" et "3,0" désignent la même réponse : grouper sur la valeur
            # parsée, pas la string brute. Fallback sur la string si une
            # vieille réponse invalide ne parse pas (ne jamais planter).
            display = format_minute_notation(row["answer"]) if q["minute_notation"] else row["answer"]
            parsed = parse_bonus_number(row["answer"])
            key = parsed if parsed is not None else display
        else:
            display = row["answer"]
            key = display
        group = by_question[row["question_id"]].setdefault(
            key, {"display": display, "names": [], "has_me": False}
        )
        is_me = row["participant_id"] == participant_id
        group["names"].append(
            {"name": row["name"], "participant_id": row["participant_id"], "is_me": is_me}
        )
        group["has_me"] = group["has_me"] or is_me

    def _group_sort_key(question):
        numeric = question["answer_type"] == "number"

        def _key(group):
            display = group["display"] or ""
            if numeric:
                parsed = parse_bonus_number(display)
                if parsed is not None:
                    return (-len(group["names"]), 0, float(parsed), "")
            return (-len(group["names"]), 1, 0.0, display)

        return _key

    return {
        qid: _build_peer_trend(groups, _group_sort_key(locked[qid]))
        for qid, groups in by_question.items()
    }


async def _load_bonus_score_breakdowns(db, questions):
    """All scored answers for questions whose result is visible.

    Positive scorers and zero-point answers are returned separately, grouped
    by answer. The list is exhaustive: every participant is named exactly
    once. Open and unscored questions are excluded before the query so no
    result can leak early.
    """
    resolved = {
        q["id"]: q
        for q in questions
        if not q["is_open"] and q["is_resolved"]
    }
    if not resolved:
        return {}

    placeholders = ",".join("?" for _ in resolved)
    rows = await db.execute(
        f"""SELECT ba.question_id, ba.participant_id, ba.answer,
                   COALESCE(NULLIF(p.nickname, ''), p.name) as name,
                   s.points
            FROM bonus_answers ba
            JOIN participants p ON p.id = ba.participant_id
            JOIN scores s ON s.bonus_question_id = ba.question_id
                         AND s.participant_id = ba.participant_id
                         AND s.id = (
                             SELECT MAX(latest.id)
                             FROM scores latest
                             WHERE latest.bonus_question_id = ba.question_id
                               AND latest.participant_id = ba.participant_id
                         )
            WHERE ba.question_id IN ({placeholders})
              AND p.is_confirmed = 1 AND p.is_admin = 0
            ORDER BY s.points DESC, name COLLATE NOCASE ASC""",
        tuple(resolved),
    )

    by_question = {
        qid: {"positive": [], "zero": []}
        for qid in resolved
    }
    group_by_question = {
        qid: {"positive": {}, "zero": {}}
        for qid in resolved
    }
    for row in await rows.fetchall():
        q = resolved[row["question_id"]]
        if q["answer_type"] == "multi_choice":
            display = format_team_list(row["answer"])
            answer_key = display
        elif q["answer_type"] == "number_multi":
            display = format_number_multi(
                row["answer"], _bonus_options_list(q["options"])
            )
            answer_key = display
        elif q["answer_type"] == "number" and q["minute_notation"]:
            display = format_minute_notation(row["answer"])
            answer_key = split_minute_notation(row["answer"])
        elif q["answer_type"] == "number":
            display = row["answer"]
            answer_key = parse_bonus_number(row["answer"])
            if answer_key is None:
                answer_key = display
        else:
            display = row["answer"]
            answer_key = display

        bucket = "positive" if row["points"] > 0 else "zero"
        key = (row["points"], answer_key)
        group = group_by_question[row["question_id"]][bucket].get(key)
        if group is None:
            group = {"display": display, "points": row["points"], "names": []}
            group_by_question[row["question_id"]][bucket][key] = group
            by_question[row["question_id"]][bucket].append(group)
        group["names"].append({
            "name": row["name"],
            "participant_id": row["participant_id"],
        })
    for breakdown in by_question.values():
        breakdown["positive"].sort(
            key=lambda group: (-group["points"], str(group["display"]).casefold())
        )
        breakdown["zero"].sort(
            key=lambda group: str(group["display"]).casefold()
        )
    return by_question


def _bonus_hub_status(item: dict) -> str:
    """Statut hub d'une question bonus ou de la carte pré-tournoi.

    Confidentialité : tant que l'item est ouvert il reste « à répondre » —
    jamais présenté comme résolu avant deadline, même si un score existe déjà.
    """
    if item.get("is_open"):
        return "open"
    if item.get("is_resolved", item.get("has_score")):
        return "resolved"
    return "waiting"


def _sanitize_open_bonus_item(item: dict) -> dict:
    """Version d'affichage d'un item encore ouvert : un score ou une bonne
    réponse saisis en avance ne doivent rien révéler avant la deadline.
    Copie défensive — les données du loader (et la base) restent intactes."""
    if (
        not item.get("has_score")
        and not item.get("is_resolved")
        and not item.get("correct_answer")
        and not item.get("correct_answer_display")
    ):
        return item
    safe = dict(item)
    safe["has_score"] = False
    safe["is_resolved"] = False
    safe["points"] = None
    safe["correct_answer"] = None
    safe["correct_answer_display"] = None
    safe.pop("score_breakdown", None)
    return safe


def _pt_value_filled(key: str, value) -> bool:
    if key == "total_goals":
        return bool(value)
    return bool(str(value or "").strip())


def _pt_has_answer(key: str, pt: dict) -> bool:
    if key == "finalist":
        return (
            bool(str(pt.get("winner") or "").strip())
            and bool(str(pt.get("finalist") or "").strip())
        )
    return _pt_value_filled(key, pt.get(key))


def _pt_has_any_answer(key: str, pt: dict) -> bool:
    """Whether a stored value exists, even if a two-pick answer is partial."""
    if key == "finalist":
        return any(
            bool(str(pt.get(field) or "").strip())
            for field in ("winner", "finalist")
        )
    return _pt_value_filled(key, pt.get(key))


def _pt_pair_display(first, second) -> str:
    values = [str(v or "").strip() for v in (first, second)]
    return " + ".join(v for v in values if v)


def _pt_answer_display(key: str, pt: dict) -> str:
    if key == "finalist":
        return _pt_pair_display(pt.get("winner"), pt.get("finalist"))
    if key == "top_scorer" and pt.get("top_scorer"):
        return normalize_scorer(pt.get("top_scorer"))
    value = pt.get(key)
    if key == "total_goals" and value:
        return str(value)
    return str(value or "").strip()


def _pt_correct_answer_display(key: str, question_map: dict) -> str:
    question = question_map.get(key) or {}
    if key == "finalist":
        return _pt_pair_display(
            (question_map.get("winner") or {}).get("correct_answer"),
            question.get("correct_answer"),
        )
    if key == "revelation":
        return format_team_list(question.get("correct_answer"))
    return str(question.get("correct_answer") or "").strip()


_PT_TREND_KEYS = ("winner", "finalist", "top_scorer", "revelation", "total_goals")


def _pt_trend_sort_key(numeric: bool):
    def _key(group):
        display = group["display"] or ""
        if numeric:
            try:
                return (-len(group["names"]), 0, float(display), "")
            except (TypeError, ValueError):
                pass
        return (-len(group["names"]), 1, 0.0, display)

    return _key


async def _load_pre_tournament_peer_trends(db, pt_status: dict, participant_id: int) -> dict:
    """Tendances collègues pour les 5 questions pré-tournoi (B7b).

    Confidentialité non négociable : tant que pt_status["open"] est vrai,
    aucune ligne n'est lue ni retournée. Le dict est alors vide (clé absente
    pour chaque question), jamais une structure preview/remaining vide qui
    pourrait laisser deviner qu'une donnée existe derrière la deadline. Ne
    lit jamais la bonne réponse ni les points déjà calculés : uniquement les
    prédictions brutes des participants confirmés non-admin.
    """
    if pt_status["open"]:
        return {}
    rows = await db.execute(
        """SELECT ptp.participant_id, ptp.winner, ptp.finalist, ptp.top_scorer,
                  ptp.revelation, ptp.total_goals,
                  COALESCE(NULLIF(p.nickname, ''), p.name) as name
           FROM pre_tournament_predictions ptp
           JOIN participants p ON p.id = ptp.participant_id
           WHERE p.is_confirmed = 1 AND p.is_admin = 0 AND ptp.submitted = 1
           ORDER BY name COLLATE NOCASE ASC"""
    )
    predictions = await rows.fetchall()

    groups_by_key = {key: {} for key in _PT_TREND_KEYS}

    def _add(key, group_key, display, pred):
        group = groups_by_key[key].setdefault(
            group_key, {"display": display, "names": [], "has_me": False}
        )
        is_me = pred["participant_id"] == participant_id
        group["names"].append(
            {"name": pred["name"], "participant_id": pred["participant_id"], "is_me": is_me}
        )
        group["has_me"] = group["has_me"] or is_me

    for pred in predictions:
        winner = str(pred["winner"] or "").strip()
        finalist = str(pred["finalist"] or "").strip()
        top_scorer = str(pred["top_scorer"] or "").strip()
        revelation = str(pred["revelation"] or "").strip()
        total_goals = pred["total_goals"]

        if winner:
            _add("winner", winner, winner, pred)
        if winner and finalist:
            pair = tuple(sorted((winner, finalist)))
            _add("finalist", pair, " + ".join(pair), pred)
        if top_scorer:
            normalized = normalize_scorer(top_scorer)
            _add("top_scorer", normalized, normalized, pred)
        if revelation:
            _add("revelation", revelation, revelation, pred)
        if total_goals:
            _add("total_goals", total_goals, str(total_goals), pred)

    return {
        key: _build_peer_trend(groups_by_key[key], _pt_trend_sort_key(key == "total_goals"))
        for key in _PT_TREND_KEYS
    }


async def _load_pre_tournament_score_breakdowns(
    db, pt_status: dict, resolved_keys
) -> dict:
    """Exhaustive positive/zero groups for visible pre-tournament results."""
    resolved_keys = set(resolved_keys)
    if pt_status["open"] or not resolved_keys:
        return {}

    placeholders = ",".join("?" for _ in resolved_keys)
    rows = await db.execute(
        f"""SELECT pts.question_key, pts.participant_id, pts.points,
                   ptp.winner, ptp.finalist, ptp.top_scorer,
                   ptp.revelation, ptp.total_goals,
                   COALESCE(NULLIF(p.nickname, ''), p.name) as name
            FROM pre_tournament_scores pts
            JOIN pre_tournament_predictions ptp
              ON ptp.participant_id = pts.participant_id
            JOIN participants p ON p.id = pts.participant_id
            WHERE pts.question_key IN ({placeholders})
              AND p.is_confirmed = 1 AND p.is_admin = 0
            ORDER BY pts.points DESC, name COLLATE NOCASE ASC""",
        tuple(resolved_keys),
    )

    by_key = {
        key: {"positive": [], "zero": []}
        for key in resolved_keys
    }
    groups_by_key = {
        key: {"positive": {}, "zero": {}}
        for key in resolved_keys
    }
    for row in await rows.fetchall():
        key = row["question_key"]
        prediction = dict(row)
        if not _pt_has_any_answer(key, prediction):
            continue
        if key == "finalist":
            pair = sorted(
                str(value).strip()
                for value in (prediction["winner"], prediction["finalist"])
                if str(value or "").strip()
            )
            display = " + ".join(pair)
        else:
            display = _pt_answer_display(key, prediction)
        bucket = "positive" if row["points"] > 0 else "zero"
        group_key = (row["points"], display)
        group = groups_by_key[key][bucket].get(group_key)
        if group is None:
            group = {"display": display, "points": row["points"], "names": []}
            groups_by_key[key][bucket][group_key] = group
            by_key[key][bucket].append(group)
        group["names"].append({
            "name": row["name"],
            "participant_id": row["participant_id"],
        })
    for breakdown in by_key.values():
        breakdown["positive"].sort(
            key=lambda group: (-group["points"], str(group["display"]).casefold())
        )
        breakdown["zero"].sort(
            key=lambda group: str(group["display"]).casefold()
        )
    return by_key


def _build_pre_tournament_bonus_cards(
    token: str,
    pt_status: dict,
    questions: list,
    score_by_key: dict,
    peer_trends: dict | None = None,
    score_breakdowns: dict | None = None,
    result_versions: dict | None = None,
) -> list:
    """Build one display card per enabled pre-tournament question for /bonus."""
    pt = pt_status.get("pt") or {}
    question_map = {q["key"]: q for q in questions}
    peer_trends = peer_trends or {}
    score_breakdowns = score_breakdowns or {}
    result_versions = result_versions or {}
    cards = []
    for q in questions:
        key = q["key"]
        score = score_by_key.get(key)
        answer_display = _pt_answer_display(key, pt)
        card = {
            "is_pre_tournament": True,
            "key": key,
            "label": PRE_TOURNAMENT_BONUS_CARD_LABELS.get(key, q["label"]),
            "question_label": q["label"],
            "phase": "pre_tournament",
            "points_label": q.get("points_label") or f"{q.get('points_value') or 0} pts",
            "answer_display": answer_display,
            "correct_answer": q.get("correct_answer"),
            "correct_answer_display": _pt_correct_answer_display(key, question_map),
            "points": score["points"] if score else None,
            "has_score": score is not None,
            "scored_at": score["calculated_at"] if score else None,
            "result_version": result_versions.get(key),
            "is_resolved": (
                not pt_status["open"]
                and (q.get("correct_answer") is not None or key in result_versions)
            ),
            "has_answer": (
                _pt_has_answer(key, pt)
                if pt_status["open"]
                else _pt_has_any_answer(key, pt)
            ),
            "is_open": pt_status["open"],
            "complete": pt_status["complete"],
            "deadline": pt_status["deadline"],
            "href": f"/p/{token}/pre-tournoi#pt-{key}",
        }
        if key in peer_trends:
            card["peer_trend"] = peer_trends[key]
        if key in score_breakdowns:
            card["score_breakdown"] = score_breakdowns[key]
        cards.append(card)
    return cards


def _as_pt_cards(pt_cards) -> list:
    if pt_cards is None:
        return []
    if isinstance(pt_cards, dict):
        return [pt_cards]
    return list(pt_cards)


def _build_bonus_hub(bonus_questions: list, pt_cards=None) -> dict:
    """Regroupe les questions bonus (+ cartes pré-tournoi) par statut pour /bonus.

    Pur view-model : aucun recalcul — les points sont ceux déjà chargés par
    _load_bonus_questions et pre_tournament_scores. Les archives (en attente,
    résolues) sont sous-groupées par étape, du plus récent au plus ancien.
    """
    buckets = {"open": [], "waiting": [], "resolved": []}
    for pt_card in _as_pt_cards(pt_cards):
        status = _bonus_hub_status(pt_card)
        buckets[status].append(
            _sanitize_open_bonus_item(pt_card) if status == "open" else pt_card
        )
    for q in bonus_questions:
        status = _bonus_hub_status(q)
        buckets[status].append(_sanitize_open_bonus_item(q) if status == "open" else q)

    phase_rank = {phase: i for i, phase in enumerate(BONUS_HUB_PHASE_ORDER)}

    def _grouped(items, with_points=False):
        by_phase = {}
        for item in items:
            by_phase.setdefault(item.get("phase"), []).append(item)
        groups = []
        for phase in sorted(by_phase, key=lambda ph: phase_rank.get(ph, len(phase_rank))):
            head = [i for i in by_phase[phase] if i.get("is_pre_tournament")]
            rest = [i for i in by_phase[phase] if not i.get("is_pre_tournament")]
            rest.sort(key=lambda i: i.get("deadline") or "", reverse=True)
            group = {"phase": phase, "entries": head + rest}
            if with_points:
                group["points"] = sum(i.get("points") or 0 for i in head + rest)
            groups.append(group)
        return groups

    return {
        "open": buckets["open"],
        "waiting": {
            "count": len(buckets["waiting"]),
            "groups": _grouped(buckets["waiting"]),
        },
        "resolved": {
            "count": len(buckets["resolved"]),
            "groups": _grouped(buckets["resolved"], with_points=True),
        },
        "counts": {
            "open": len(buckets["open"]),
            "waiting": len(buckets["waiting"]),
            "resolved": len(buckets["resolved"]),
        },
    }


def _bonus_result_identity(item: dict) -> tuple[str, str]:
    """(source, source_key) stable pour bonus_result_views. À n'appeler que sur
    un item déjà "resolved" du hub (score visible) — jamais sur open/waiting."""
    if item.get("is_pre_tournament"):
        return "pre_tournament", item["key"]
    return "bonus_question", str(item["id"])


async def _load_bonus_result_views(db, participant_id: int) -> dict:
    """{(source, source_key): result_version vu} pour ce participant."""
    rows = await db.execute(
        """SELECT source, source_key, result_version
           FROM bonus_result_views WHERE participant_id=?""",
        (participant_id,),
    )
    return {(r["source"], r["source_key"]): r["result_version"] for r in await rows.fetchall()}


def _apply_new_result_flags(hub: dict, views: dict) -> list:
    """Marque is_new_result sur chaque item de hub["resolved"] (mutation en place
    des objets réels — jamais hub["open"]/["waiting"], déjà masqués en amont par
    _build_bonus_hub/_sanitize_open_bonus_item). Retourne, dans l'ordre d'affichage
    de la page (groupes triés par phase, carte pré-tournoi en tête de groupe), la
    liste des items nouveaux."""
    new_items = []
    for group in hub["resolved"]["groups"]:
        for item in group["entries"]:
            if not item.get("has_answer") and not item.get("has_score"):
                item["is_new_result"] = False
                continue
            source, source_key = _bonus_result_identity(item)
            seen_version = views.get((source, source_key))
            current_version = item.get("result_version") or item.get("scored_at") or ""
            item["is_new_result"] = seen_version is None or seen_version != current_version
            if item["is_new_result"]:
                new_items.append(item)
    return new_items


def _bonus_item_needs_answer(item: dict) -> bool:
    """Un item "ouvert" (hub["open"]) reste modifiable jusqu'à sa deadline mais
    peut déjà avoir été répondu (B5 : les 5 cartes pré-tournoi restent ouvertes
    jusqu'à leur deadline même une fois remplies) — seule l'absence de réponse
    justifie une "action à faire". Question bonus classique (_load_bonus_questions)
    et carte pré-tournoi (_build_pre_tournament_bonus_cards / _pt_has_answer, qui
    exige winner ET finalist remplis pour la carte "Finalistes") exposent toutes
    deux le même champ `has_answer`, pas de traitement séparé nécessaire."""
    return not item["has_answer"]


def _build_bonus_situation(hub: dict, total_bonus_points: int, phase_labels: dict,
                            new_items: list | None = None) -> dict:
    """View-model du bloc "Ma situation Bonus" : dérivé uniquement du hub déjà
    sanitizé (points visibles), aucun accès DB ni recalcul de scoring. Le détail
    par phase reprend tel quel hub["resolved"]["groups"] : une carte pré-tournoi
    n'y entre jamais tant que sa deadline est ouverte (cf. _build_bonus_hub /
    sanitisation B5), donc pas de fuite possible via ce détail.

    new_items (B8) : items déjà flaggés is_new_result par _apply_new_result_flags,
    dans l'ordre d'affichage de la page. S'il y en a, la prochaine action priorise
    toujours "voir les nouveaux résultats" avant "à répondre"/"en attente"."""
    phase_breakdown = [
        {
            "phase": g["phase"],
            "label": phase_labels.get(g["phase"], g["phase"]),
            "points": g["points"],
        }
        for g in hub["resolved"]["groups"]
    ]
    counts = hub["counts"]
    todo_count = sum(1 for item in hub["open"] if _bonus_item_needs_answer(item))
    new_items = new_items or []
    new_result_count = len(new_items)
    if new_result_count > 0:
        first = new_items[0]
        href = (
            f"#bonus-pt-{first['key']}"
            if first.get("is_pre_tournament")
            else f"#bonus-q-{first['id']}"
        )
        next_action = {
            "state": "new_result",
            "text": (
                "Nouveau résultat bonus disponible."
                if new_result_count == 1
                else f"{new_result_count} nouveaux résultats bonus disponibles."
            ),
            "href": href,
        }
    elif todo_count > 0:
        next_action = {
            "state": "todo",
            "text": f"{todo_count} question{'s' if todo_count > 1 else ''} à répondre",
            "href": "#bonus-hub-open",
        }
    elif counts["waiting"] > 0:
        next_action = {"state": "waiting", "text": "À jour — résultats en attente", "href": None}
    else:
        next_action = {"state": "done", "text": "À jour", "href": None}
    return {
        "total_points": total_bonus_points,
        "phase_breakdown": phase_breakdown,
        "counts": counts,
        "todo_count": todo_count,
        "new_result_count": new_result_count,
        "next_action": next_action,
    }


async def _mark_bonus_results_seen(db, participant_id: int, hub: dict) -> None:
    """Enregistre, pour les PROCHAINES visites de /bonus, la version vue de chaque
    résultat actuellement affiché dans hub["resolved"]. À appeler seulement après
    avoir construit `situation`/le contexte de rendu : les badges de CETTE requête
    ne doivent jamais dépendre de cet appel. N'écrit jamais pour hub["open"]/["waiting"]."""
    now = _now_utc()
    rows = [
        (
            participant_id,
            *_bonus_result_identity(item),
            item.get("result_version") or item.get("scored_at") or "",
            now,
        )
        for group in hub["resolved"]["groups"]
        for item in group["entries"]
        if item.get("has_answer") or item.get("has_score")
    ]
    if not rows:
        return
    await db.executemany(
        """INSERT INTO bonus_result_views
             (participant_id, source, source_key, result_version, seen_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(participant_id, source, source_key) DO UPDATE SET
             result_version=excluded.result_version,
             seen_at=excluded.seen_at""",
        rows,
    )
    await db.commit()


@router.get("/p/{token}/bonus", response_class=HTMLResponse)
async def bonus_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "bonus")
        p = ctx["participant"]
        now = _now_utc()
        bonus_questions, pending_count = await _load_bonus_questions(db, p["id"], now)
        peer_answers = await _load_bonus_peer_answers(db, bonus_questions, p["id"])
        score_breakdowns = await _load_bonus_score_breakdowns(db, bonus_questions)
        for q in bonus_questions:
            if not q["is_open"]:
                q["peer_answers"] = peer_answers.get(q["id"], {"preview": [], "remaining": []})
            if q["id"] in score_breakdowns:
                q["score_breakdown"] = score_breakdowns[q["id"]]
        pt_status = await _pt_status(db, p["id"])
        pt_score_rows = await db.execute(
            """SELECT pts.question_key, pts.points, pts.calculated_at
               FROM pre_tournament_scores pts
               JOIN pre_tournament_questions ptq ON ptq.key = pts.question_key
               WHERE pts.participant_id=? AND ptq.is_enabled=1""",
            (p["id"],),
        )
        raw_pt_scores = {
            r["question_key"]: dict(r) for r in await pt_score_rows.fetchall()
        }
        raw_pt_points = sum(score["points"] or 0 for score in raw_pt_scores.values())
        if pt_status["open"]:
            visible_pt_scores = {}
            visible_pt_points = 0
            visible_pt_scored = False
        else:
            visible_pt_scores = raw_pt_scores
            visible_pt_points = raw_pt_points
            visible_pt_scored = bool(raw_pt_scores)
        bq_score_row = await db.execute(
            """SELECT COALESCE(SUM(s.points), 0) as total
               FROM scores s
               JOIN bonus_questions bq ON bq.id = s.bonus_question_id
               WHERE s.participant_id=?
                 AND bq.is_published=1
                 AND datetime(bq.deadline) <= datetime(?)
                 AND s.id = (
                     SELECT MAX(latest.id)
                     FROM scores latest
                     WHERE latest.participant_id = s.participant_id
                       AND latest.bonus_question_id = s.bonus_question_id
                 )""",
            (p["id"], now),
        )
        bonus_questions_points = (await bq_score_row.fetchone())["total"]
        pt_cards = []
        if pt_status["question_count"] > 0:
            pt_questions = await get_pre_tournament_questions(db)
            pt_version_rows = await db.execute(
                """SELECT question_key, MAX(calculated_at) AS result_version
                   FROM pre_tournament_scores
                   GROUP BY question_key"""
            )
            pt_result_versions = {
                row["question_key"]: row["result_version"]
                for row in await pt_version_rows.fetchall()
            }
            pt_resolved_keys = {
                question["key"]
                for question in pt_questions
                if question.get("correct_answer") is not None
                or question["key"] in pt_result_versions
            }
            pt_peer_trends = await _load_pre_tournament_peer_trends(db, pt_status, p["id"])
            pt_score_breakdowns = await _load_pre_tournament_score_breakdowns(
                db, pt_status, pt_resolved_keys
            )
            pt_cards = _build_pre_tournament_bonus_cards(
                token,
                pt_status,
                pt_questions,
                visible_pt_scores,
                pt_peer_trends,
                pt_score_breakdowns,
                pt_result_versions,
            )
        phase_labels = {"pre_tournament": "Pré-tournoi", **PHASE_LABELS}
        total_bonus_points = bonus_questions_points + visible_pt_points
        hub = _build_bonus_hub(bonus_questions, pt_cards)

        # B8 — flags calculés APRÈS le masquage de confidentialité (uniquement sur
        # hub["resolved"]) : jamais sur un item encore ouvert ou non scoré.
        result_views = await _load_bonus_result_views(db, p["id"])
        new_result_items = _apply_new_result_flags(hub, result_views)

        situation = _build_bonus_situation(
            hub, total_bonus_points, phase_labels, new_items=new_result_items
        )
        ctx.update({
            "bonus_questions": bonus_questions,
            "pending_bonus_questions": pending_count,
            "now": now,
            "phase_labels": phase_labels,
            "pt_points": visible_pt_points,
            "pt_scored": visible_pt_scored,
            "bonus_questions_points": bonus_questions_points,
            "total_bonus_points": total_bonus_points,
            "hub": hub,
            "situation": situation,
        })

        # Marquage "vu" pour les PROCHAINES visites seulement : après avoir déjà
        # calculé is_new_result/situation pour CETTE requête (les badges restent
        # visibles sur la page rendue courante ; ils disparaîtront au refresh suivant).
        await _mark_bonus_results_seen(db, p["id"], hub)
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
            multi_select_config = normalize_multi_select_config(
                q["scoring_config"]
            )
            selected_set = set(selected)
            all_or_nothing = (
                selected_set
                & multi_select_config["all_or_nothing_options"]
            )
            if all_or_nothing and len(selected_set) > 1:
                option = sorted(all_or_nothing)[0].split(" — ", 1)[0]
                raise HTTPException(
                    400,
                    f"L'option « {option} » doit être cochée seule",
                )
            answer = json.dumps(selected, ensure_ascii=False)
        elif q["answer_type"] == "number_multi":
            options = json.loads(q["options"]) if q["options"] else []
            config = normalize_number_multi_config(
                q["points_value"],
                q["scoring_config"],
                options,
            )
            valid_options = set(options)
            locked = set(config["locked_teams"])
            raw_count = (form.get("count") or "").strip()
            try:
                count = int(raw_count)
            except ValueError:
                raise HTTPException(400, "Total invalide")
            if count < config["min_count"] or count > config["max_count"]:
                raise HTTPException(
                    400,
                    f"Total hors limites ({config['min_count']} à {config['max_count']})",
                )
            selected = {v for v in form.getlist("teams") if v in valid_options}
            selected |= locked
            if len(selected) != count:
                raise HTTPException(400, f"Sélectionne exactement {count} équipe(s)")
            ordered = [opt for opt in options if opt in selected]
            for team in sorted(selected - set(ordered)):
                ordered.append(team)
            answer = json.dumps({"count": count, "teams": ordered}, ensure_ascii=False)
        else:
            if q["answer_type"] == "number" and normalize_closest_config(
                q["points_value"], q["scoring_config"]
            ).get("minute_notation"):
                answer = compose_minute_notation(form.get("minute", ""), form.get("added", "")) or ""
            else:
                answer = (form.get("answer") or "").strip()
            if not answer:
                raise HTTPException(400, "Réponse vide")
            if q["answer_type"] == "choice" and q["options"]:
                options = json.loads(q["options"])
                if answer not in options:
                    raise HTTPException(400, "Réponse invalide")
            if q["answer_type"] == "number":
                parsed_number = parse_bonus_number(answer)
                if parsed_number is None:
                    raise HTTPException(400, "Réponse numérique invalide")
                closest_config = normalize_closest_config(
                    q["points_value"],
                    q["scoring_config"],
                )
                if closest_config.get("integer_only") and not bonus_number_is_integer(parsed_number):
                    raise HTTPException(400, "Réponse entière requise")
                min_value = closest_config.get("min_value")
                max_value = closest_config.get("max_value")
                if min_value is not None and parsed_number < min_value:
                    raise HTTPException(400, f"Réponse trop basse (minimum {min_value})")
                if max_value is not None and parsed_number > max_value:
                    raise HTTPException(400, f"Réponse trop haute (maximum {max_value})")
        await db.execute(
            """INSERT INTO bonus_answers (participant_id, question_id, answer)
               VALUES (?,?,?)
               ON CONFLICT(participant_id, question_id) DO UPDATE SET answer=excluded.answer""",
            (p["id"], question_id, answer)
        )
        await db.commit()
    if request.headers.get("x-resa-bonus-stack") == "1":
        return JSONResponse({"ok": True, "question_id": question_id})
    return RedirectResponse(url=f"/p/{token}/bonus", status_code=303)
