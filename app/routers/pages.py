"""Participant-facing HTML page routes."""
from collections import defaultdict
import io
import logging
import os
import uuid

from fastapi import APIRouter, File, HTTPException, Request, Form, UploadFile
from PIL import Image, ImageOps
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import hash_password, require_participant, verify_password
from app.config import settings
from app.constants import DEPARTMENTS, MIN_PASSWORD_LENGTH
from app.database import get_db
from app.flags import team_flag
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
from app.settings_store import knockout_predictions_open
from app.scoring import (
    get_department_rankings,
    get_rank_evolution,
    get_rankings,
    get_remontada,
    is_match_prediction_correct,
    is_match_score_exact,
    predicted_match_winner,
)
from app.templating import create_templates
from app.timeutils import (
    is_match_locked,
    local_today,
    match_kickoff_utc,
    minutes_until_match,
    now_utc_iso,
    utc_day_bounds_for_local_date,
)

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
    1: "Phase de groupes - Match 1",
    2: "Phase de groupes - Match 2",
    3: "Phase de groupes - Match 3",
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


# Durée max estimée d'un match (90' + arrêts + prolongations + TAB).
LIVE_WINDOW_MINUTES = 150


def _live_state(match: dict) -> str:
    """'' (à venir) | 'live' | 'awaiting' (joué, résultat pas encodé) | 'done'."""
    if not is_match_locked(match):
        return ""
    if match.get("result") is not None:
        return "done"
    return "live" if minutes_until_match(match) >= -LIVE_WINDOW_MINUTES else "awaiting"


def _minutes_until(match: dict) -> int:
    return minutes_until_match(match)


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


def _qualifier_label(match: dict) -> str:
    if match.get("qualifier_prediction") == "team1":
        return match["team1_name"]
    if match.get("qualifier_prediction") == "team2":
        return match["team2_name"]
    return ""


def _enrich_prediction_matches(matches: list[dict]) -> None:
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
        sections.append({
            "key": key,
            "label": PREDICTION_SECTION_LABELS[key],
            "short_label": PREDICTION_SECTION_LABELS[key].replace("Phase de groupes - ", ""),
            "total": total,
            "done": done,
            "open_count": open_count,
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
        "group_match_1": "Match 1 regroupe le premier match de chaque équipe dans son groupe.",
        "group_match_2": "Match 2 regroupe le deuxième match de chaque équipe dans son groupe.",
        "group_match_3": "Match 3 regroupe le troisième match de chaque équipe dans son groupe.",
    }.get(
        section_key,
        "Les phases finales valent ×2. Les points portent sur l'équipe qui se qualifie.",
    )


async def _get_participant_context(token: str, db, active_nav: str = "home") -> dict:
    """Build common context for participant templates."""
    p = await require_participant(token)
    # Get rank + total points
    rankings = await get_rankings(db)
    rank = next((r for r in rankings if r["id"] == p["id"]), None)
    total_points = rank["total_points"] if rank else 0
    user_rank = rank["rank"] if rank else "—"
    # Pending bonus questions
    bonus_row = await db.execute(
        """SELECT COUNT(*) as cnt FROM bonus_questions bq
           WHERE bq.deadline > ? AND NOT EXISTS (
             SELECT 1 FROM bonus_answers ba
             WHERE ba.question_id = bq.id AND ba.participant_id = ?
           )""",
        (_now_utc(), p["id"])
    )
    pending_bonus = (await bonus_row.fetchone())["cnt"]
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
    }


# ---- Login / registration ----

@router.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {
        "request": request, "error": None, "email": ""
    })


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
    return RedirectResponse(url=f"/p/{participant['token']}", status_code=303)


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
                    password_hash, department)
                   VALUES (?,?,?,?,?,1,?,?)""",
                (name, first_name, last_name, email, token,
                 hash_password(password), department)
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
        # Upcoming/today matches
        today_start_utc, today_end_utc = utc_day_bounds_for_local_date()
        rows = await db.execute(
            """SELECT m.*,
                 p.prediction, p.exact_score_team1, p.exact_score_team2,
                 s.points
               FROM matches m
               LEFT JOIN predictions p ON p.match_id = m.id AND p.participant_id = ?
               LEFT JOIN scores s ON s.match_id = m.id AND s.participant_id = ?
               WHERE datetime(m.match_date || 'T' || m.kickoff_time) >= datetime(?)
                 AND datetime(m.match_date || 'T' || m.kickoff_time) <= datetime(?)
               ORDER BY m.kickoff_time""",
            (p["id"], p["id"], today_start_utc, today_end_utc)
        )
        today_matches = [dict(r) for r in await rows.fetchall()]
        for m in today_matches:
            m["is_locked"] = _is_locked(m)
            m["live_state"] = _live_state(m)
        # Urgency match: next unpredicted/locked-soon match
        urgency = None
        for m in today_matches:
            mins = _minutes_until(m)
            if not m["is_locked"] and m.get("prediction") is None and 0 < mins < 300:
                m["mins_until"] = mins
                m["kickoff_ts"] = int(match_kickoff_utc(m).timestamp())
                urgency = m
                break
        # Unmatched today alert
        unpredicted_today = sum(1 for m in today_matches if not m["is_locked"] and not m.get("prediction"))
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
        # Encoded count
        encoded_row = await db.execute("SELECT COUNT(*) as cnt FROM matches WHERE result IS NOT NULL")
        encoded_count = (await encoded_row.fetchone())["cnt"]
        # Récap d'hier: points gagnés sur les matchs de la veille déjà encodés
        y_start, y_end = utc_day_bounds_for_local_date(local_today(-1))
        y_row = await db.execute(
            """SELECT COALESCE(SUM(s.points), 0) AS pts, COUNT(m.id) AS cnt
               FROM matches m
               LEFT JOIN scores s ON s.match_id = m.id AND s.participant_id = ?
               WHERE m.result IS NOT NULL
                 AND datetime(m.match_date || 'T' || m.kickoff_time) >= datetime(?)
                 AND datetime(m.match_date || 'T' || m.kickoff_time) <= datetime(?)""",
            (p["id"], y_start, y_end)
        )
        yesterday = dict(await y_row.fetchone())
        my_evolution = (await get_rank_evolution(db)).get(p["id"])
        # Pre-tournament status
        pt_status = await _pt_status(db, p["id"])
        all_caught_up = (
            urgency is None
            and unpredicted_today == 0
            and (not pt_status["open"] or pt_status["complete"])
            and ctx["pending_bonus"] == 0
        )
        ctx.update({
            "today_matches": today_matches,
            "urgency": urgency,
            "unpredicted_today": unpredicted_today,
            "next_match": next_match,
            "all_caught_up": all_caught_up,
            "yesterday": yesterday,
            "my_evolution": my_evolution,
            "mini_rank": mini_rank,
            "rival_ahead": rival_ahead,
            "rival_behind": rival_behind,
            "encoded_count": encoded_count,
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
        await db.execute(
            """UPDATE participants
               SET name = ?, first_name = ?, last_name = ?, is_confirmed = 1,
                   department = COALESCE(NULLIF(?, ''), department)
               WHERE token = ?""",
            (name, first_name, last_name, department, token)
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
        knockout_open = await knockout_predictions_open(db)
        for match in all_matches:
            match["pronos_closed"] = match["phase"] != "group" and not knockout_open
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
            "knockout_open": knockout_open,
            "page_wide": True,
        })
    return templates.TemplateResponse(request, "predictions.html", {"request": request, **ctx})


@router.get("/p/{token}/reglement", response_class=HTMLResponse)
async def rules_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "rules")
        ctx["page_wide"] = True
        ctx["prize_info"] = await get_prize_info(db)
        ctx["knockout_open"] = await knockout_predictions_open(db)
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


@router.get("/p/{token}/classement", response_class=HTMLResponse)
async def ranking_page(request: Request, token: str, view: str = "general"):
    if view not in RANKING_VIEWS:
        view = "general"
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "rank")
        p = ctx["participant"]
        rankings = []
        departments = []
        evolution = {}
        if view == "remontada":
            rankings = await get_remontada(db)
        elif view == "departments":
            departments = await get_department_rankings(db)
        else:
            rankings = await get_rankings(db, scope=view)
            if view == "general":
                evolution = await get_rank_evolution(db)
        for r in rankings:
            r["is_me"] = (r["id"] == p["id"])
            r["color_class"] = f"c{((r['id'] - 1) % 8) + 1}"
            r["evolution"] = evolution.get(r["id"])
        # La remontada n'a de sens qu'une fois la phase finale entamée.
        ko_row = await db.execute(
            "SELECT COUNT(*) AS cnt FROM matches WHERE phase != 'group' AND result IS NOT NULL"
        )
        knockout_started = (await ko_row.fetchone())["cnt"] > 0
        prize_info = await get_prize_info(db)
        my_department = (p.get("department") or "").strip() or "Sans département"
        ctx.update({
            "rankings": rankings,
            "departments": departments,
            "my_department": my_department,
            "view": view,
            "ranking_views": RANKING_VIEWS,
            "knockout_started": knockout_started,
            "prize_info": prize_info,
        })
    return templates.TemplateResponse(request, "ranking.html", {"request": request, **ctx})


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
        ctx.update({
            "match": match_dict,
            "match_phase_label": PHASE_LABELS.get(match["phase"], match["phase"]),
            "my_pred": dict(my_pred) if my_pred else None,
            "my_score": dict(my_score) if my_score else None,
            "dist": dist,
            "dist_total": dist_total,
            "all_preds": all_preds,
        })
    return templates.TemplateResponse(request, "match_detail.html", {"request": request, **ctx})


@router.get("/p/{token}/profil", response_class=HTMLResponse)
async def own_profile(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "profil")
        p = ctx["participant"]
        profile_data = await _build_profile(p["id"], db)
        ctx.update({"profile": profile_data, "is_own": True})
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
async def other_profile(request: Request, token: str, participant_id: int):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "")
        p = ctx["participant"]
        row = await db.execute("SELECT * FROM participants WHERE id=? AND is_confirmed=1", (participant_id,))
        target = await row.fetchone()
        if not target:
            raise HTTPException(404)
        profile_data = await _build_profile(participant_id, db, viewer_id=p["id"])
        ctx.update({"profile": profile_data, "is_own": participant_id == p["id"]})
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
        """SELECT m.phase, m.score_team1, m.score_team2, m.result, m.qualifier_winner,
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
    # Streak: consecutive correct predictions (latest first)
    streak_row = await db.execute(
        """SELECT m.phase, m.score_team1, m.score_team2, m.result, m.qualifier_winner,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  pr.qualifier_prediction
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=? AND m.result IS NOT NULL
           ORDER BY m.match_date DESC, m.kickoff_time DESC""",
        (participant_id,)
    )
    streak_preds = await streak_row.fetchall()
    streak = 0
    for sp in streak_preds:
        sp_dict = dict(sp)
        if is_match_prediction_correct(sp_dict, sp_dict):
            streak += 1
        else:
            break
    # Best day
    best_day_row = await db.execute(
        """SELECT m.group_name, SUM(s.points) as day_points
           FROM scores s
           JOIN matches m ON m.id = s.match_id
           WHERE s.participant_id=?
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
    # Équipe la plus souvent donnée gagnante (équipes réelles uniquement)
    fav_rows = await db.execute(
        """SELECT CASE WHEN pr.prediction='team1' THEN m.team1_name
                       ELSE m.team2_name END AS team, COUNT(*) AS cnt
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=? AND pr.prediction != 'draw'
           GROUP BY team ORDER BY cnt DESC LIMIT 8""",
        (participant_id,)
    )
    favorite_pick = next(
        ((r["team"], r["cnt"]) for r in await fav_rows.fetchall() if team_flag(r["team"])),
        None,
    )
    # Nuls tentés / réussis
    draw_row = await db.execute(
        "SELECT COUNT(*) AS cnt FROM predictions WHERE participant_id=? AND prediction='draw'",
        (participant_id,)
    )
    draw_attempts = (await draw_row.fetchone())["cnt"]
    draw_correct = sum(
        1 for r in played_predictions
        if r["phase"] == "group" and r["prediction"] == "draw" and r["result"] == "draw"
    )
    # Roi des bonus: 1er du classement bonus avec des points
    bonus_rankings = await get_rankings(db, scope="bonus")
    bonus_king = bool(
        bonus_rankings
        and bonus_rankings[0]["total_points"] > 0
        and bonus_rankings[0]["rank"] == 1
        and bonus_rankings[0]["id"] == participant_id
    )
    badges = [
        {"key": "sniper", "icon": "🎯", "label": "Sniper",
         "desc": "5 scores exacts trouvés",
         "unlocked": exact >= 5},
        {"key": "streak", "icon": "🔥", "label": "En série",
         "desc": "4 bons pronos d'affilée",
         "unlocked": streak >= 4},
        {"key": "loyal", "icon": "🛡️", "label": "Fidèle au poste",
         "desc": "Tous les matchs joués pronostiqués (min. 5)",
         "unlocked": total_results >= 5 and total_played >= total_results},
        {"key": "draw_king", "icon": "🤝", "label": "Roi du nul",
         "desc": "3 matchs nuls trouvés",
         "unlocked": draw_correct >= 3},
        {"key": "last_minute", "icon": "⏱️", "label": "Dernière minute",
         "desc": "5 pronos dans l'heure avant le coup d'envoi",
         "unlocked": last_minute_count >= 5},
        {"key": "bonus_king", "icon": "⭐", "label": "Roi des bonus",
         "desc": "1er au classement bonus",
         "unlocked": bonus_king},
    ]
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
        "best_day": best_day,
        "best_day_pts": best_day_pts,
        "last5": [] if is_limited_view else last5,
        "recent_form": [] if is_limited_view else recent_form,
        "badges": badges,
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
async def submit_bonus(request: Request, token: str, question_id: int,
                       answer: str = Form(...)):
    p = await require_participant(token)
    async with get_db() as db:
        q_row = await db.execute("SELECT * FROM bonus_questions WHERE id=?", (question_id,))
        q = await q_row.fetchone()
        if not q:
            raise HTTPException(404)
        if q["deadline"] < _now_utc():
            raise HTTPException(403, "Deadline dépassée")
        await db.execute(
            """INSERT INTO bonus_answers (participant_id, question_id, answer)
               VALUES (?,?,?)
               ON CONFLICT(participant_id, question_id) DO UPDATE SET answer=excluded.answer""",
            (p["id"], question_id, answer)
        )
        await db.commit()
    return RedirectResponse(url=f"/p/{token}/bonus", status_code=303)
