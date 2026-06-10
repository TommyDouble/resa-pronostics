"""Participant-facing HTML page routes."""
from collections import defaultdict
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import require_participant
from app.database import get_db
from app.players import (
    TEAMS_48,
    get_scorer_options,
    is_valid_scorer,
    normalize_scorer,
)
from app.pre_tournament import (
    get_pre_tournament_deadline,
    get_pre_tournament_question_map,
    get_pre_tournament_questions,
)
from app.scoring import get_rankings
from app.templating import create_templates
from app.timeutils import (
    is_match_locked,
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


def _split_name(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


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


def _is_locked(match: dict) -> bool:
    return is_match_locked(match)


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
        open_count = sum(1 for m in section_matches if not m.get("is_locked"))
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
        "pending_bonus": pending_bonus,
        "active_nav": active_nav,
        "token": token,
    }


@router.get("/rejoindre", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"request": request, "error": None})


@router.post("/rejoindre", response_class=HTMLResponse)
async def register_post(request: Request, name: str = Form(...), email: str = Form(...)):
    name = name.strip()
    email = email.strip().lower()
    if not name or not email or "@" not in email:
        return templates.TemplateResponse(request, "register.html", {
            "request": request, "error": "Nom et email valides requis."
        })
    token = str(uuid.uuid4())
    first_name, last_name = _split_name(name)
    async with get_db() as db:
        # Check if email already registered
        existing = await (await db.execute(
            "SELECT token FROM participants WHERE email=?", (email,)
        )).fetchone()
        if existing:
            return RedirectResponse(url=f"/p/{existing['token']}", status_code=303)
        try:
            await db.execute(
                """INSERT INTO participants
                   (name, first_name, last_name, email, token, is_confirmed)
                   VALUES (?,?,?,?,?,1)""",
                (name, first_name, last_name, email, token)
            )
            await db.commit()
        except Exception:
            return templates.TemplateResponse(request, "register.html", {
                "request": request, "error": "Une erreur est survenue, réessaie."
            })
    return RedirectResponse(url=f"/p/{token}", status_code=303)


@router.get("/p/{token}", response_class=HTMLResponse)
async def participant_home(request: Request, token: str):
    p = await require_participant(token)
    if not p["is_confirmed"]:
        return templates.TemplateResponse(request, "onboarding.html", {
            "request": request, "participant": dict(p), "token": token
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
        # Mini ranking (top 3 + self)
        rankings = await get_rankings(db)
        mini_rank = rankings[:3]
        me_rank = next((r for r in rankings if r["id"] == p["id"]), None)
        if me_rank and me_rank["rank"] > 3:
            mini_rank.append(me_rank)
        # Encoded count
        encoded_row = await db.execute("SELECT COUNT(*) as cnt FROM matches WHERE result IS NOT NULL")
        encoded_count = (await encoded_row.fetchone())["cnt"]
        # Pre-tournament status
        pt_row2 = await db.execute(
            "SELECT submitted FROM pre_tournament_predictions WHERE participant_id = ?", (p["id"],)
        )
        pt2 = await pt_row2.fetchone()
        pt_submitted = bool(pt2 and pt2["submitted"])
        pt_deadline = await get_pre_tournament_deadline(db)
        pt_open = _now_utc() < pt_deadline
        ctx.update({
            "today_matches": today_matches,
            "urgency": urgency,
            "unpredicted_today": unpredicted_today,
            "mini_rank": mini_rank,
            "encoded_count": encoded_count,
            "pt_submitted": pt_submitted,
            "pt_open": pt_open,
            "pt_deadline": pt_deadline,
        })
    return templates.TemplateResponse(request, "home.html", {"request": request, **ctx})


@router.post("/p/{token}/confirm", response_class=HTMLResponse)
async def confirm_onboarding(request: Request, token: str,
                              first_name: str = Form(...), last_name: str = Form(...)):
    p = await require_participant(token)
    name = f"{first_name.strip()} {last_name.strip()}"
    async with get_db() as db:
        await db.execute(
            """UPDATE participants
               SET name = ?, first_name = ?, last_name = ?, is_confirmed = 1
               WHERE token = ?""",
            (name, first_name.strip(), last_name.strip(), token)
        )
        await db.commit()
    return RedirectResponse(url=f"/p/{token}", status_code=303)


@router.get("/p/{token}/pronos", response_class=HTMLResponse)
async def predictions_page(request: Request, token: str, section: str = "", phase: str = ""):
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
        sections = _prediction_sections(all_matches)
        requested_section = section
        if not requested_section and phase:
            requested_section = "group_match_1" if phase == "group" else phase
        if requested_section not in PREDICTION_SECTION_ORDER:
            requested_section = _default_prediction_section(sections)
        current_matches = [
            match for match in all_matches
            if match.get("section_key") == requested_section
        ]
        pt_row = await db.execute(
            "SELECT submitted FROM pre_tournament_predictions WHERE participant_id = ?", (p["id"],)
        )
        pt = await pt_row.fetchone()
        pt_submitted = bool(pt and pt["submitted"])
        ctx.update({
            "matches": all_matches,
            "current_matches": current_matches,
            "current_section": requested_section,
            "current_section_label": PREDICTION_SECTION_LABELS.get(requested_section, requested_section),
            "prediction_sections": sections,
            "phase_labels": PHASE_LABELS,
            "pt_submitted": pt_submitted,
            "page_wide": True,
        })
    return templates.TemplateResponse(request, "predictions.html", {"request": request, **ctx})


@router.get("/p/{token}/reglement", response_class=HTMLResponse)
async def rules_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "rules")
        ctx["page_wide"] = True
    return templates.TemplateResponse(request, "rules.html", {"request": request, **ctx})


PT_ERRORS = {
    "winner_finalist": "Le finaliste doit être différent du vainqueur.",
    "invalid_team": "Une des équipes choisies est invalide.",
    "invalid_scorer": "Le joueur choisi est invalide.",
}


@router.get("/p/{token}/pre-tournoi", response_class=HTMLResponse)
async def pre_tournament_page(request: Request, token: str, error: str = ""):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "bonus")
        p = ctx["participant"]
        row = await db.execute(
            "SELECT * FROM pre_tournament_predictions WHERE participant_id = ?", (p["id"],)
        )
        pt = await row.fetchone()
        outsiders = ["Maroc", "Japon", "États-Unis", "Sénégal", "Australie", "Iran", "Côte d'Ivoire", "Équateur"]
        pt_deadline = await get_pre_tournament_deadline(db)
        pt_questions = await get_pre_tournament_question_map(db)
        pt_editable = _now_utc() < pt_deadline
        pt_dict = dict(pt) if pt else {}
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
    action: str = Form(default="draft"),
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
    for team_value in (winner, finalist, revelation):
        if team_value and team_value not in TEAMS_48:
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
        submitted = 1 if action == "submit" else 0
        submitted_at = _now_utc() if submitted else None
        if existing:
            await db.execute(
                """UPDATE pre_tournament_predictions
                   SET winner=?, finalist=?, top_scorer=?, revelation=?, total_goals=?,
                       submitted=?, submitted_at=?
                   WHERE participant_id=?""",
                (values["winner"], values["finalist"], values["top_scorer"], values["revelation"], values["total_goals"],
                 submitted, submitted_at, p["id"])
            )
        else:
            await db.execute(
                """INSERT INTO pre_tournament_predictions
                   (participant_id, winner, finalist, top_scorer, revelation, total_goals, submitted, submitted_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (p["id"], values["winner"], values["finalist"], values["top_scorer"], values["revelation"], values["total_goals"], submitted, submitted_at)
            )
        await db.commit()
    return RedirectResponse(url=f"/p/{token}/pre-tournoi", status_code=303)


@router.get("/p/{token}/classement", response_class=HTMLResponse)
async def ranking_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "rank")
        rankings = await get_rankings(db)
        p = ctx["participant"]
        # Annotate own position
        for r in rankings:
            r["is_me"] = (r["id"] == p["id"])
            r["color_class"] = f"c{((r['id'] - 1) % 8) + 1}"
        ctx.update({"rankings": rankings})
    return templates.TemplateResponse(request, "ranking.html", {"request": request, **ctx})


@router.get("/p/{token}/match/{match_id}", response_class=HTMLResponse)
async def match_detail_page(request: Request, token: str, match_id: int):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db)
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
        # Community distribution
        dist_row = await db.execute(
            "SELECT prediction, COUNT(*) as cnt FROM predictions WHERE match_id=? GROUP BY prediction",
            (match_id,)
        )
        dist_raw = {r["prediction"]: r["cnt"] for r in await dist_row.fetchall()}
        total_preds = sum(dist_raw.values()) or 1
        dist = {k: {"cnt": v, "pct": round(v / total_preds * 100)} for k, v in dist_raw.items()}
        # All predictions (only after kickoff)
        all_preds_rows = await db.execute(
            """SELECT par.name, pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
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
            "match": dict(match),
            "match_phase_label": PHASE_LABELS.get(match["phase"], match["phase"]),
            "my_pred": dict(my_pred) if my_pred else None,
            "my_score": dict(my_score) if my_score else None,
            "dist": dist,
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


@router.get("/p/{token}/profil/edit", response_class=HTMLResponse)
async def edit_profile_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "profil")
        ctx.update({"teams": TEAMS_48})
    return templates.TemplateResponse(request, "profile_edit.html", {"request": request, **ctx})


@router.post("/p/{token}/profil/edit", response_class=HTMLResponse)
async def save_profile(
    request: Request,
    token: str,
    first_name: str = Form(default=""),
    last_name: str = Form(default=""),
    nickname: str = Form(default=""),
    favorite_team: str = Form(default=""),
    bio: str = Form(default=""),
    profile_visibility: str = Form(default="public"),
    email_opt_in: str = Form(default="0"),
):
    p = await require_participant(token)
    first_name = first_name.strip()[:80]
    last_name = last_name.strip()[:80]
    nickname = nickname.strip()[:40]
    favorite_team = favorite_team.strip()[:80]
    bio = bio.strip()[:240]
    if profile_visibility not in ("public", "limited"):
        profile_visibility = "public"
    full_name = f"{first_name} {last_name}".strip() or p["name"]
    async with get_db() as db:
        await db.execute(
            """UPDATE participants
               SET name=?, first_name=?, last_name=?, nickname=?, favorite_team=?,
                   bio=?, profile_visibility=?, email_opt_in=?
               WHERE token=?""",
            (
                full_name,
                first_name,
                last_name,
                nickname,
                favorite_team,
                bio,
                profile_visibility,
                1 if email_opt_in == "1" else 0,
                token,
            ),
        )
        await db.commit()
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
        """SELECT
             COUNT(*) as total,
             SUM(CASE WHEN pr.prediction = m.result THEN 1 ELSE 0 END) as correct,
             SUM(CASE WHEN pr.exact_score_team1 = m.score_team1
                         AND pr.exact_score_team2 = m.score_team2
                         AND pr.exact_score_team1 IS NOT NULL
                      THEN 1 ELSE 0 END) as exact
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=? AND m.result IS NOT NULL""",
        (participant_id,)
    )
    stats = await played_row.fetchone()
    total_played = stats["total"] or 0
    correct = stats["correct"] or 0
    exact = stats["exact"] or 0
    success_rate = round(correct / total_played * 100) if total_played else 0
    # Streak: consecutive correct predictions (latest first)
    streak_row = await db.execute(
        """SELECT pr.prediction, m.result
           FROM predictions pr
           JOIN matches m ON m.id = pr.match_id
           WHERE pr.participant_id=? AND m.result IS NOT NULL
           ORDER BY m.match_date DESC, m.kickoff_time DESC""",
        (participant_id,)
    )
    streak_preds = await streak_row.fetchall()
    streak = 0
    for sp in streak_preds:
        if sp["prediction"] == sp["result"]:
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
        """SELECT m.team1_name, m.team2_name, m.score_team1, m.score_team2, m.result,
                  pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                  COALESCE(s.points, 0) as points
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
        if m["prediction"] == m["result"] and m.get("exact_score_team1") == m.get("score_team1") and m.get("exact_score_team1") is not None:
            m["result_chip"] = "exact"
        elif m["prediction"] == m["result"]:
            m["result_chip"] = "ok"
        else:
            m["result_chip"] = "miss"
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
        "full_name": p["name"],
        "nickname": p.get("nickname"),
        "favorite_team": "" if is_limited_view else p.get("favorite_team"),
        "bio": "" if is_limited_view else p.get("bio"),
        "profile_visibility": p.get("profile_visibility") or "public",
        "email_opt_in": p.get("email_opt_in"),
        "is_limited_view": is_limited_view,
        "initials": initials,
        "color_class": color_class,
        "rank": user_rank,
        "total_points": total_points,
        "match_count": match_count,
        "success_rate": success_rate,
        "exact_count": exact,
        "streak": streak,
        "best_day": best_day,
        "best_day_pts": best_day_pts,
        "last5": [] if is_limited_view else last5,
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
