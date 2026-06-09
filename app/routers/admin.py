"""Admin backoffice routes."""
import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_admin, verify_password
from app.config import settings
from app.database import get_db
from app.datetime_utils import (
    local_input_to_utc,
    match_to_local_label,
    utc_to_local_input,
    utc_to_local_label,
    utc_to_local_short,
)
from app.mail import (
    send_email,
    send_invitation,
    send_match_reminder as send_match_reminder_email,
    send_pre_tournament_reminder,
)
from app.routers.pages import SCORERS, TEAMS_48
from app.pre_tournament import (
    DEFAULT_PRE_TOURNAMENT_QUESTIONS,
    get_pre_tournament_deadline,
    get_pre_tournament_question_map,
    get_pre_tournament_questions,
)
from app.scoring import (
    calculate_bonus_scores,
    get_rankings,
    recalculate_match_scores,
    recalculate_pre_tournament_scores,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["localdt"] = utc_to_local_label
templates.env.filters["localinput"] = utc_to_local_input
templates.env.filters["localshort"] = utc_to_local_short
logger = logging.getLogger(__name__)

PHASE_LABELS = {
    "group": "Phase de groupes",
    "round_of_32": "Seizièmes",
    "round_of_16": "Huitièmes",
    "quarter": "Quarts",
    "semi": "Demies",
    "third_place": "3e place",
    "final": "Finale",
}

BONUS_PHASES = {"pre_tournament", "round_of_32", "quarter", "semi"}
OUTSIDERS = ["Maroc", "Japon", "USA", "Sénégal", "Australie", "Iran", "Côte d'Ivoire", "Équateur"]

STATUSES = {
    "confirmed": ("ok", "confirmé"),
    "invited": ("warn", "invité"),
    "pending": ("gr", "en attente"),
}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _is_played(match: dict) -> bool:
    try:
        kickoff = datetime.fromisoformat(
            f"{match['match_date']}T{match['kickoff_time']}"
        ).replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= kickoff
    except Exception:
        return False


def _flash(request: Request, msg: str, kind: str = "ok"):
    request.session.setdefault("flashes", []).append({"msg": msg, "kind": kind})


def _get_flashes(request: Request):
    return request.session.pop("flashes", [])


def _split_name(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _normalize_bonus_options(answer_type: str, options_text: str):
    if answer_type != "choice":
        return None
    options = [
        opt.strip()
        for line in options_text.splitlines()
        for opt in line.split(",")
        if opt.strip()
    ]
    return json.dumps(options, ensure_ascii=False) if options else None


def _decorate_match_times(matches: list[dict]):
    for match in matches:
        match["kickoff_display"] = match_to_local_label(match)


# ---- Login ----

@router.get("/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    if request.session.get("admin_id"):
        return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse("admin/login.html", {
        "request": request, "error": request.session.pop("login_error", None)
    })


@router.post("/login", response_class=HTMLResponse)
async def admin_login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    async with get_db() as db:
        row = await db.execute("SELECT * FROM admin_users WHERE username=?", (username,))
        admin = await row.fetchone()
    if admin and verify_password(password, admin["password_hash"]):
        request.session["admin_id"] = admin["id"]
        return RedirectResponse("/admin/dashboard", status_code=303)
    request.session["login_error"] = "Identifiants incorrects."
    return RedirectResponse("/admin/login", status_code=303)


@router.post("/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def admin_root(request: Request):
    return RedirectResponse("/admin/dashboard")


# ---- Dashboard ----

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    await require_admin(request)
    async with get_db() as db:
        now = _now_utc()
        # KPIs
        total_row = await db.execute("SELECT COUNT(*) as cnt FROM participants WHERE is_admin=0 AND is_active=1")
        total_participants = (await total_row.fetchone())["cnt"]
        confirmed_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1 AND is_admin=0 AND is_active=1"
        )
        confirmed = (await confirmed_row.fetchone())["cnt"]
        pt_row = await db.execute(
            """SELECT COUNT(*) as cnt FROM pre_tournament_predictions pt
               JOIN participants p ON p.id=pt.participant_id
               WHERE pt.submitted=1 AND p.is_active=1 AND p.is_admin=0"""
        )
        pt_submitted = (await pt_row.fetchone())["cnt"]
        has_pred_row = await db.execute(
            """SELECT COUNT(DISTINCT pr.participant_id) as cnt
               FROM predictions pr
               JOIN participants p ON p.id=pr.participant_id
               WHERE p.is_active=1 AND p.is_admin=0"""
        )
        has_pred = (await has_pred_row.fetchone())["cnt"]
        next_row = await db.execute(
            """SELECT match_number, team1_name, team2_name
               FROM matches WHERE result IS NULL AND match_date >= ?
               ORDER BY match_date, kickoff_time LIMIT 1""",
            (now[:10],)
        )
        next_match = await next_row.fetchone()
        # 5 last encoded
        last_enc = await db.execute(
            """SELECT match_number, team1_name, team2_name, score_team1, score_team2, created_at
               FROM matches WHERE result IS NOT NULL
               ORDER BY created_at DESC LIMIT 5"""
        )
        last_encoded = [dict(r) for r in await last_enc.fetchall()]
        # Alert: matches played >2h without result
        alert_row = await db.execute(
            """SELECT COUNT(*) as cnt FROM matches
               WHERE result IS NULL
               AND datetime(match_date || 'T' || kickoff_time) < datetime(?, '-2 hours')""",
            (now,)
        )
        late_matches = (await alert_row.fetchone())["cnt"]
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "active": "dashboard",
        "flashes": _get_flashes(request),
        "total_participants": total_participants,
        "confirmed": confirmed,
        "pt_submitted": pt_submitted,
        "has_pred": has_pred,
        "next_match": dict(next_match) if next_match else None,
        "last_encoded": last_encoded,
        "late_matches": late_matches,
    })


# ---- Participants ----

@router.get("/participants", response_class=HTMLResponse)
async def participants_list(request: Request, show: str = Query(default="active")):
    await require_admin(request)
    if show not in ("active", "inactive"):
        show = "active"
    active_value = 0 if show == "inactive" else 1
    async with get_db() as db:
        rows = await db.execute(
            """SELECT p.*,
                 (SELECT COUNT(*) FROM predictions WHERE participant_id=p.id) as pred_count,
                 (SELECT submitted FROM pre_tournament_predictions WHERE participant_id=p.id) as pt_submitted
               FROM participants p WHERE p.is_admin=0 AND p.is_active=?
               ORDER BY p.created_at"""
            ,
            (active_value,),
        )
        participants = [dict(r) for r in await rows.fetchall()]
    return templates.TemplateResponse("admin/participants.html", {
        "request": request,
        "active": "participants",
        "flashes": _get_flashes(request),
        "participants": participants,
        "base_url": request.base_url,
        "show": show,
    })


@router.post("/participants/add")
async def add_participant(request: Request, name: str = Form(...), email: str = Form(...)):
    await require_admin(request)
    name = name.strip()
    email = email.strip().lower()
    first_name, last_name = _split_name(name)
    token = str(uuid.uuid4())
    participant = {"name": name, "email": email, "token": token}
    async with get_db() as db:
        try:
            await db.execute(
                """INSERT INTO participants (name, first_name, last_name, email, token)
                   VALUES (?,?,?,?,?)""",
                (name, first_name, last_name, email, token)
            )
            await db.commit()
            sent = await send_invitation(participant)
            if sent:
                _flash(request, f"Participant {name} ajouté. Invitation envoyée.")
            else:
                _flash(
                    request,
                    f"Participant {name} ajouté. Lien généré, email non envoyé (SMTP absent ou erreur).",
                    "warn",
                )
        except Exception as e:
            if "UNIQUE" in str(e):
                _flash(request, "Email déjà utilisé.", "err")
            else:
                _flash(request, "Erreur lors de l'ajout.", "err")
    return RedirectResponse("/admin/participants", status_code=303)


@router.post("/participants/{participant_id}/invite")
async def invite_participant(request: Request, participant_id: int):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute(
            "SELECT name, email, token FROM participants WHERE id=? AND is_admin=0 AND is_active=1",
            (participant_id,)
        )
        participant = await row.fetchone()
    if not participant:
        raise HTTPException(404)
    sent = await send_invitation(dict(participant))
    if sent:
        _flash(request, f"Invitation envoyée à {participant['email']}.")
    else:
        _flash(
            request,
            f"Email non envoyé à {participant['email']}. Copie le lien personnel depuis la liste.",
            "warn",
        )
    return RedirectResponse("/admin/participants", status_code=303)


@router.post("/participants/{participant_id}/update")
async def update_participant(
    request: Request,
    participant_id: int,
    name: str = Form(...),
    email: str = Form(...),
):
    await require_admin(request)
    name = name.strip()
    email = email.strip().lower()
    if not name or "@" not in email:
        _flash(request, "Nom ou email invalide.", "err")
        return RedirectResponse("/admin/participants", status_code=303)
    first_name, last_name = _split_name(name)
    async with get_db() as db:
        try:
            await db.execute(
                """UPDATE participants
                   SET name=?, first_name=?, last_name=?, email=?
                   WHERE id=? AND is_admin=0""",
                (name, first_name, last_name, email, participant_id),
            )
            await db.commit()
            _flash(request, "Participant mis à jour.")
        except Exception as e:
            if "UNIQUE" in str(e):
                _flash(request, "Email déjà utilisé.", "err")
            else:
                _flash(request, "Erreur lors de la mise à jour.", "err")
    return RedirectResponse("/admin/participants", status_code=303)


@router.post("/participants/{participant_id}/toggle-paid")
async def toggle_paid(request: Request, participant_id: int):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT has_paid FROM participants WHERE id=?", (participant_id,))
        p = await row.fetchone()
        if not p:
            raise HTTPException(404)
        new_val = 0 if p["has_paid"] else 1
        await db.execute("UPDATE participants SET has_paid=? WHERE id=?", (new_val, participant_id))
        await db.commit()
    return RedirectResponse("/admin/participants", status_code=303)


@router.post("/participants/{participant_id}/delete")
async def delete_participant(request: Request, participant_id: int):
    await require_admin(request)
    async with get_db() as db:
        await db.execute(
            "UPDATE participants SET is_active=0 WHERE id=? AND is_admin=0",
            (participant_id,),
        )
        await db.commit()
    _flash(request, "Participant désactivé. Son lien personnel est bloqué.")
    return RedirectResponse("/admin/participants", status_code=303)


@router.post("/participants/{participant_id}/reactivate")
async def reactivate_participant(request: Request, participant_id: int):
    await require_admin(request)
    async with get_db() as db:
        await db.execute(
            "UPDATE participants SET is_active=1 WHERE id=? AND is_admin=0",
            (participant_id,),
        )
        await db.commit()
    _flash(request, "Participant réactivé.")
    return RedirectResponse("/admin/participants?show=inactive", status_code=303)


@router.post("/participants/import")
async def import_csv(request: Request, csv_file: UploadFile = File(...)):
    await require_admin(request)
    content = await csv_file.read()
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except Exception:
        _flash(request, "Impossible de lire le CSV.", "err")
        return RedirectResponse("/admin/participants", status_code=303)

    errors = []
    valid = []
    for i, row in enumerate(rows, 1):
        name = row.get("nom", row.get("name", "")).strip()
        email = row.get("email", "").strip().lower()
        if not name or not email or "@" not in email:
            errors.append(f"Ligne {i}: nom ou email invalide")
        else:
            valid.append((name, email))

    if errors:
        _flash(request, f"Erreurs CSV: {'; '.join(errors[:3])}", "err")
        return RedirectResponse("/admin/participants", status_code=303)

    imported_participants = []
    async with get_db() as db:
        for name, email in valid:
            first_name, last_name = _split_name(name)
            token = str(uuid.uuid4())
            try:
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO participants
                       (name, first_name, last_name, email, token)
                       VALUES (?,?,?,?,?)""",
                    (name, first_name, last_name, email, token)
                )
                if cursor.rowcount:
                    imported_participants.append({"name": name, "email": email, "token": token})
            except Exception:
                pass
        await db.commit()
    sent_count = 0
    for participant in imported_participants:
        if await send_invitation(participant):
            sent_count += 1
    if sent_count == len(imported_participants):
        _flash(request, f"{len(imported_participants)} participant(s) importé(s), {sent_count} invitation(s) envoyée(s).")
    else:
        _flash(
            request,
            f"{len(imported_participants)} participant(s) importé(s), {sent_count} email(s) envoyé(s). Liens disponibles dans la liste.",
            "warn",
        )
    return RedirectResponse("/admin/participants", status_code=303)


# ---- Prediction Review ----

@router.get("/pronostics", response_class=HTMLResponse)
async def predictions_admin(
    request: Request,
    view: str = Query(default="matches"),
    participant_id: int = Query(default=0),
    phase: str = Query(default="all"),
):
    await require_admin(request)
    if view not in ("matches", "pre_tournament", "bonus"):
        view = "matches"
    async with get_db() as db:
        p_rows = await db.execute(
            """SELECT id, name, nickname FROM participants
               WHERE is_admin=0 AND is_active=1
               ORDER BY COALESCE(NULLIF(nickname, ''), name)"""
        )
        participants = [dict(r) for r in await p_rows.fetchall()]
        match_predictions = []
        pre_tournament_rows = []
        bonus_answers = []
        pt_questions = await get_pre_tournament_question_map(db, include_disabled=True)

        if view == "matches":
            where = ["p.is_admin=0", "p.is_active=1"]
            params = []
            if participant_id:
                where.append("p.id=?")
                params.append(participant_id)
            if phase != "all":
                where.append("m.phase=?")
                params.append(phase)
            rows = await db.execute(
                f"""SELECT
                      p.id as participant_id,
                      COALESCE(NULLIF(p.nickname, ''), p.name) as participant_name,
                      p.name as full_name,
                      m.match_number, m.phase, m.team1_name, m.team2_name,
                      m.score_team1, m.score_team2, m.result,
                      pr.prediction, pr.exact_score_team1, pr.exact_score_team2,
                      pr.submitted_at, COALESCE(s.points, 0) as points
                    FROM predictions pr
                    JOIN participants p ON p.id = pr.participant_id
                    JOIN matches m ON m.id = pr.match_id
                    LEFT JOIN scores s ON s.match_id = pr.match_id
                      AND s.participant_id = pr.participant_id
                    WHERE {' AND '.join(where)}
                    ORDER BY pr.submitted_at DESC, m.match_number""",
                params,
            )
            match_predictions = [dict(r) for r in await rows.fetchall()]

        if view == "pre_tournament":
            where = ["p.is_admin=0", "p.is_active=1"]
            params = []
            if participant_id:
                where.append("p.id=?")
                params.append(participant_id)
            rows = await db.execute(
                f"""SELECT
                      p.id as participant_id,
                      COALESCE(NULLIF(p.nickname, ''), p.name) as participant_name,
                      p.name as full_name,
                      pt.winner, pt.finalist, pt.top_scorer, pt.revelation,
                      pt.total_goals, pt.submitted, pt.submitted_at,
                      COALESCE(pts.points, 0) as pre_tournament_points
                    FROM participants p
                    LEFT JOIN pre_tournament_predictions pt
                      ON pt.participant_id = p.id
                    LEFT JOIN (
                      SELECT participant_id, SUM(points) as points
                      FROM pre_tournament_scores
                      GROUP BY participant_id
                    ) pts ON pts.participant_id = p.id
                    WHERE {' AND '.join(where)}
                    ORDER BY p.name""",
                params,
            )
            pre_tournament_rows = [dict(r) for r in await rows.fetchall()]

        if view == "bonus":
            where = ["p.is_admin=0", "p.is_active=1"]
            params = []
            if participant_id:
                where.append("p.id=?")
                params.append(participant_id)
            rows = await db.execute(
                f"""SELECT
                      p.id as participant_id,
                      COALESCE(NULLIF(p.nickname, ''), p.name) as participant_name,
                      p.name as full_name,
                      bq.question_text, bq.phase, bq.points_value,
                      bq.correct_answer, ba.answer, ba.submitted_at,
                      COALESCE(s.points, 0) as points
                    FROM bonus_answers ba
                    JOIN participants p ON p.id = ba.participant_id
                    JOIN bonus_questions bq ON bq.id = ba.question_id
                    LEFT JOIN scores s ON s.bonus_question_id = ba.question_id
                      AND s.participant_id = ba.participant_id
                    WHERE {' AND '.join(where)}
                    ORDER BY ba.submitted_at DESC""",
                params,
            )
            bonus_answers = [dict(r) for r in await rows.fetchall()]

    return templates.TemplateResponse("admin/predictions.html", {
        "request": request,
        "active": "pronostics",
        "flashes": _get_flashes(request),
        "view": view,
        "participant_id": participant_id,
        "phase": phase,
        "participants": participants,
        "phase_labels": PHASE_LABELS,
        "match_predictions": match_predictions,
        "pre_tournament_rows": pre_tournament_rows,
        "bonus_answers": bonus_answers,
        "pt_questions": pt_questions,
    })


# ---- Pre-tournament Admin ----

@router.get("/pre-tournoi", response_class=HTMLResponse)
async def pre_tournament_admin(request: Request):
    await require_admin(request)
    async with get_db() as db:
        questions = await get_pre_tournament_questions(db, include_disabled=True)
        deadline = await get_pre_tournament_deadline(db)
        submitted_row = await db.execute(
            """SELECT COUNT(*) as cnt FROM pre_tournament_predictions pt
               JOIN participants p ON p.id=pt.participant_id
               WHERE pt.submitted=1 AND p.is_active=1 AND p.is_admin=0"""
        )
        submitted_count = (await submitted_row.fetchone())["cnt"]
        total_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1 AND is_admin=0 AND is_active=1"
        )
        total_count = (await total_row.fetchone())["cnt"]
        answer_rows = await db.execute("SELECT key, answer FROM pre_tournament_official_answers")
        official_answers = {r["key"]: r["answer"] for r in await answer_rows.fetchall()}
        scored_row = await db.execute(
            """SELECT COUNT(DISTINCT ps.participant_id) as cnt, COALESCE(SUM(ps.points), 0) as pts
               FROM pre_tournament_scores ps
               JOIN participants p ON p.id=ps.participant_id
               WHERE p.is_active=1 AND p.is_admin=0"""
        )
        scored = await scored_row.fetchone()
    return templates.TemplateResponse("admin/pre_tournament.html", {
        "request": request,
        "active": "pre_tournoi",
        "flashes": _get_flashes(request),
        "questions": questions,
        "deadline": deadline,
        "deadline_input": utc_to_local_input(deadline),
        "deadline_display": utc_to_local_label(deadline),
        "submitted_count": submitted_count,
        "total_count": total_count,
        "official_answers": official_answers,
        "pre_tournament_scored_count": scored["cnt"] if scored else 0,
        "pre_tournament_total_points": scored["pts"] if scored else 0,
        "teams": TEAMS_48,
        "scorers": sorted(SCORERS),
        "outsiders": OUTSIDERS,
    })


@router.post("/pre-tournoi/deadline")
async def update_pre_tournament_deadline(request: Request, deadline: str = Form(...)):
    await require_admin(request)
    deadline = deadline.strip()
    if "T" not in deadline:
        _flash(request, "Deadline invalide.", "err")
        return RedirectResponse("/admin/pre-tournoi", status_code=303)
    try:
        deadline_utc = local_input_to_utc(deadline)
    except ValueError:
        _flash(request, "Deadline invalide.", "err")
        return RedirectResponse("/admin/pre-tournoi", status_code=303)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO app_settings (key, value) VALUES ('pre_tournament_deadline', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (deadline_utc,)
        )
        await db.commit()
    _flash(request, "Deadline pré-tournoi mise à jour.")
    return RedirectResponse("/admin/pre-tournoi", status_code=303)


@router.post("/pre-tournoi/questions/{question_key}")
async def update_pre_tournament_question(
    request: Request,
    question_key: str,
    label: str = Form(...),
    points_label: str = Form(...),
    help_text: str = Form(default=""),
    is_enabled: str = Form(default="0"),
):
    await require_admin(request)
    allowed = {q["key"] for q in DEFAULT_PRE_TOURNAMENT_QUESTIONS}
    if question_key not in allowed:
        raise HTTPException(404)
    async with get_db() as db:
        await db.execute(
            """UPDATE pre_tournament_questions
               SET label=?, points_label=?, help_text=?, is_enabled=?
               WHERE key=?""",
            (
                label.strip()[:120],
                points_label.strip()[:80],
                help_text.strip()[:240],
                1 if is_enabled == "1" else 0,
                question_key,
            ),
        )
        await db.commit()
    _flash(request, "Question pré-tournoi mise à jour.")
    return RedirectResponse("/admin/pre-tournoi", status_code=303)


@router.post("/pre-tournoi/official")
async def update_pre_tournament_official_answers(
    request: Request,
    winner: str = Form(default=""),
    finalist: str = Form(default=""),
    top_scorer: str = Form(default=""),
    revelation: str = Form(default=""),
    total_goals: str = Form(default=""),
):
    await require_admin(request)
    answers = {
        "winner": winner.strip(),
        "finalist": finalist.strip(),
        "top_scorer": top_scorer.strip(),
        "revelation": revelation.strip(),
        "total_goals": total_goals.strip(),
    }
    if answers["total_goals"]:
        try:
            int(answers["total_goals"])
        except ValueError:
            _flash(request, "Total de buts officiel invalide.", "err")
            return RedirectResponse("/admin/pre-tournoi", status_code=303)
    async with get_db() as db:
        for key, value in answers.items():
            await db.execute(
                """INSERT INTO pre_tournament_official_answers (key, answer, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                     answer=excluded.answer,
                     updated_at=datetime('now')""",
                (key, value or None),
            )
        await db.commit()
    await recalculate_pre_tournament_scores()
    _flash(request, "Réponses officielles enregistrées. Scores pré-tournoi recalculés.")
    return RedirectResponse("/admin/pre-tournoi", status_code=303)


# ---- Matches ----

@router.get("/matches", response_class=HTMLResponse)
async def matches_list(request: Request, phase: str = "group"):
    await require_admin(request)
    async with get_db() as db:
        rows = await db.execute(
            "SELECT * FROM matches WHERE phase=? ORDER BY match_date, kickoff_time",
            (phase,)
        )
        matches = [dict(r) for r in await rows.fetchall()]
        _decorate_match_times(matches)
        counts = {}
        for ph in PHASE_LABELS:
            c_row = await db.execute("SELECT COUNT(*) as cnt FROM matches WHERE phase=?", (ph,))
            counts[ph] = (await c_row.fetchone())["cnt"]
    return templates.TemplateResponse("admin/matches.html", {
        "request": request,
        "active": "matches",
        "flashes": _get_flashes(request),
        "matches": matches,
        "current_phase": phase,
        "phase_labels": PHASE_LABELS,
        "phase_counts": counts,
    })


@router.post("/matches/{match_id}/toggle-top")
async def toggle_top_match(request: Request, match_id: int):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        if _is_played(dict(match)):
            return {"error": "Match déjà joué"}, 400
        new_val = 0 if match["is_top_match"] else 1
        new_weight = 2 if new_val else 1
        await db.execute(
            "UPDATE matches SET is_top_match=?, weight=? WHERE id=?",
            (new_val, new_weight, match_id)
        )
        await db.commit()
    return {"is_top_match": bool(new_val)}


# ---- Results ----

@router.get("/resultats", response_class=HTMLResponse)
async def results_page(request: Request):
    await require_admin(request)
    async with get_db() as db:
        now = _now_utc()
        # Matches played but no result
        pending_rows = await db.execute(
            """SELECT * FROM matches
               WHERE result IS NULL
               AND datetime(match_date || 'T' || kickoff_time) <= datetime(?)
               ORDER BY match_date, kickoff_time""",
            (now,)
        )
        pending = [dict(r) for r in await pending_rows.fetchall()]
        _decorate_match_times(pending)
        # Already encoded (last 10)
        done_rows = await db.execute(
            """SELECT * FROM matches WHERE result IS NOT NULL
               ORDER BY match_date DESC, kickoff_time DESC LIMIT 10"""
        )
        done = [dict(r) for r in await done_rows.fetchall()]
        _decorate_match_times(done)
    return templates.TemplateResponse("admin/results.html", {
        "request": request,
        "active": "resultats",
        "flashes": _get_flashes(request),
        "pending": pending,
        "done": done,
    })


@router.post("/resultats/{match_id}")
async def encode_result(request: Request, match_id: int,
                        score_team1: int = Form(...), score_team2: int = Form(...)):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        result = "team1" if score_team1 > score_team2 else ("team2" if score_team2 > score_team1 else "draw")
        await db.execute(
            "UPDATE matches SET score_team1=?, score_team2=?, result=? WHERE id=?",
            (score_team1, score_team2, result, match_id)
        )
        await db.commit()
    await recalculate_match_scores(match_id)
    _flash(request, f"Résultat encodé. Scores recalculés.")
    return RedirectResponse("/admin/resultats", status_code=303)


@router.post("/resultats/{match_id}/correct")
async def correct_result(request: Request, match_id: int,
                         score_team1: int = Form(...), score_team2: int = Form(...)):
    await require_admin(request)
    async with get_db() as db:
        result = "team1" if score_team1 > score_team2 else ("team2" if score_team2 > score_team1 else "draw")
        await db.execute(
            "UPDATE matches SET score_team1=?, score_team2=?, result=? WHERE id=?",
            (score_team1, score_team2, result, match_id)
        )
        await db.commit()
    await recalculate_match_scores(match_id)
    _flash(request, "Correction appliquée. Scores recalculés.")
    return RedirectResponse("/admin/resultats", status_code=303)


# ---- Bonus Questions ----

@router.get("/bonus", response_class=HTMLResponse)
async def bonus_admin(request: Request):
    await require_admin(request)
    async with get_db() as db:
        pt_deadline = await get_pre_tournament_deadline(db)
        rows = await db.execute(
            """SELECT bq.*,
                      COUNT(ba.id) as answer_count,
                      SUM(CASE WHEN bq.correct_answer IS NOT NULL
                                AND ba.answer = bq.correct_answer THEN 1 ELSE 0 END) as correct_count
               FROM bonus_questions bq
               LEFT JOIN bonus_answers ba ON ba.question_id = bq.id
               GROUP BY bq.id
               ORDER BY bq.deadline"""
        )
        questions = [dict(r) for r in await rows.fetchall()]
        for question in questions:
            try:
                opts = json.loads(question["options"]) if question.get("options") else []
            except Exception:
                opts = []
            question["options_text"] = "\n".join(opts)
            question["correct_count"] = question.get("correct_count") or 0
            question["deadline_input"] = utc_to_local_input(question["deadline"])
            question["deadline_display"] = utc_to_local_label(question["deadline"])
        answer_rows = await db.execute(
            """SELECT
                  ba.question_id,
                  COALESCE(NULLIF(p.nickname, ''), p.name) as participant_name,
                  p.name as full_name,
                  ba.answer,
                  ba.submitted_at,
                  COALESCE(s.points, 0) as points
               FROM bonus_answers ba
               JOIN participants p ON p.id = ba.participant_id
               LEFT JOIN scores s ON s.bonus_question_id = ba.question_id
                 AND s.participant_id = ba.participant_id
               WHERE p.is_admin=0 AND p.is_active=1
               ORDER BY ba.submitted_at DESC"""
        )
        answers_by_question = {}
        for answer in await answer_rows.fetchall():
            answer_dict = dict(answer)
            answers_by_question.setdefault(answer_dict["question_id"], []).append(answer_dict)
        pt_sub_row = await db.execute(
            """SELECT COUNT(*) as cnt FROM pre_tournament_predictions pt
               JOIN participants p ON p.id=pt.participant_id
               WHERE pt.submitted=1 AND p.is_active=1 AND p.is_admin=0"""
        )
        pt_submitted_count = (await pt_sub_row.fetchone())["cnt"]
        pt_total_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1 AND is_admin=0 AND is_active=1"
        )
        pt_total_count = (await pt_total_row.fetchone())["cnt"]
    return templates.TemplateResponse("admin/bonus.html", {
        "request": request,
        "active": "bonus",
        "flashes": _get_flashes(request),
        "questions": questions,
        "phase_labels": PHASE_LABELS,
        "pt_submitted_count": pt_submitted_count,
        "pt_total_count": pt_total_count,
        "pt_deadline": pt_deadline,
        "answers_by_question": answers_by_question,
    })


@router.post("/bonus/create")
async def create_bonus(request: Request,
                       question_text: str = Form(...), phase: str = Form(...),
                       answer_type: str = Form(...), points_value: int = Form(...),
                       deadline: str = Form(...), options_text: str = Form(default=""),
                       correct_answer: str = Form(default="")):
    await require_admin(request)
    if answer_type not in ("choice", "number", "text"):
        _flash(request, "Type de question invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    if phase not in BONUS_PHASES:
        _flash(request, "Phase de question bonus invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    try:
        deadline_utc = local_input_to_utc(deadline)
    except ValueError:
        _flash(request, "Deadline invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    options = _normalize_bonus_options(answer_type, options_text)
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO bonus_questions
               (question_text, phase, answer_type, options, points_value, correct_answer, deadline)
               VALUES (?,?,?,?,?,?,?)""",
            (
                question_text.strip(),
                phase,
                answer_type,
                options,
                points_value,
                correct_answer.strip() or None,
                deadline_utc,
            )
        )
        question_id = cursor.lastrowid
        await db.commit()
    if correct_answer.strip():
        await calculate_bonus_scores(question_id)
    _flash(request, "Question créée.")
    return RedirectResponse("/admin/bonus", status_code=303)


@router.post("/bonus/{question_id}/update")
async def update_bonus_question(
    request: Request,
    question_id: int,
    question_text: str = Form(...),
    phase: str = Form(...),
    answer_type: str = Form(...),
    points_value: int = Form(...),
    deadline: str = Form(...),
    options_text: str = Form(default=""),
    correct_answer: str = Form(default=""),
):
    await require_admin(request)
    if answer_type not in ("choice", "number", "text"):
        _flash(request, "Type de question invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    if phase not in BONUS_PHASES:
        _flash(request, "Phase de question bonus invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    try:
        deadline_utc = local_input_to_utc(deadline)
    except ValueError:
        _flash(request, "Deadline invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    options = _normalize_bonus_options(answer_type, options_text)
    async with get_db() as db:
        row = await db.execute("SELECT id FROM bonus_questions WHERE id=?", (question_id,))
        if not await row.fetchone():
            raise HTTPException(404)
        await db.execute(
            """UPDATE bonus_questions
               SET question_text=?, phase=?, answer_type=?, options=?,
                   points_value=?, correct_answer=?, deadline=?
               WHERE id=?""",
            (
                question_text.strip(),
                phase,
                answer_type,
                options,
                points_value,
                correct_answer.strip() or None,
                deadline_utc,
                question_id,
            ),
        )
        await db.commit()
    await calculate_bonus_scores(question_id)
    _flash(request, "Question mise à jour.")
    return RedirectResponse("/admin/bonus", status_code=303)


@router.post("/bonus/{question_id}/answer")
async def set_bonus_answer(request: Request, question_id: int,
                            correct_answer: str = Form(...)):
    await require_admin(request)
    async with get_db() as db:
        await db.execute(
            "UPDATE bonus_questions SET correct_answer=? WHERE id=?",
            (correct_answer, question_id)
        )
        await db.commit()
    await calculate_bonus_scores(question_id)
    _flash(request, "Réponse correcte enregistrée. Scores calculés.")
    return RedirectResponse("/admin/bonus", status_code=303)


@router.post("/bonus/{question_id}/delete")
async def delete_bonus_question(request: Request, question_id: int):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT id FROM bonus_questions WHERE id=?", (question_id,))
        if not await row.fetchone():
            raise HTTPException(404)
        await db.execute("DELETE FROM bonus_questions WHERE id=?", (question_id,))
        await db.commit()
    _flash(request, "Question supprimée.")
    return RedirectResponse("/admin/bonus", status_code=303)


# ---- Communications ----

@router.get("/communications", response_class=HTMLResponse)
async def communications(request: Request):
    await require_admin(request)
    async with get_db() as db:
        # Count non-submitted pre-tournament
        ns_row = await db.execute(
            """SELECT COUNT(*) as cnt FROM participants p
               WHERE p.is_confirmed=1 AND p.is_admin=0 AND p.is_active=1
               AND NOT EXISTS (SELECT 1 FROM pre_tournament_predictions pt
                               WHERE pt.participant_id=p.id AND pt.submitted=1)"""
        )
        no_pt = (await ns_row.fetchone())["cnt"]
        # Upcoming matches
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        m_rows = await db.execute(
            "SELECT * FROM matches WHERE match_date >= ? AND result IS NULL ORDER BY match_date, kickoff_time LIMIT 20",
            (today,)
        )
        upcoming = [dict(r) for r in await m_rows.fetchall()]
        _decorate_match_times(upcoming)
    return templates.TemplateResponse("admin/communications.html", {
        "request": request,
        "active": "communications",
        "flashes": _get_flashes(request),
        "no_pt": no_pt,
        "upcoming_matches": upcoming,
        "smtp_host": settings.SMTP_HOST,
        "smtp_port": settings.SMTP_PORT,
        "smtp_from": settings.SMTP_FROM,
        "smtp_configured": bool(settings.SMTP_HOST),
    })


@router.post("/communications/send-pt-reminder")
async def send_pt_reminder(request: Request):
    await require_admin(request)
    async with get_db() as db:
        rows = await db.execute(
            """SELECT p.name, p.email, p.token FROM participants p
               WHERE p.is_confirmed=1 AND p.is_admin=0 AND p.is_active=1 AND p.email_opt_in=1
               AND NOT EXISTS (SELECT 1 FROM pre_tournament_predictions pt
                               WHERE pt.participant_id=p.id AND pt.submitted=1)"""
        )
        participants = [dict(r) for r in await rows.fetchall()]
    sent_count = 0
    for participant in participants:
        if await send_pre_tournament_reminder(participant):
            sent_count += 1
    _flash(request, f"{sent_count}/{len(participants)} rappel(s) pré-tournoi envoyé(s).")
    return RedirectResponse("/admin/communications", status_code=303)


@router.post("/communications/send-match-reminder")
async def send_match_reminder(request: Request, match_id: int = Form(...)):
    await require_admin(request)
    async with get_db() as db:
        match_row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await match_row.fetchone()
        if not match:
            raise HTTPException(404)
        if _is_played(dict(match)):
            _flash(request, "Ce match a déjà commencé.", "err")
            return RedirectResponse("/admin/communications", status_code=303)
        rows = await db.execute(
            """SELECT p.name, p.email, p.token FROM participants p
               WHERE p.is_confirmed=1 AND p.is_admin=0 AND p.is_active=1 AND p.email_opt_in=1
               AND NOT EXISTS (SELECT 1 FROM predictions pr
                               WHERE pr.participant_id=p.id AND pr.match_id=?)""",
            (match_id,)
        )
        participants = [dict(r) for r in await rows.fetchall()]
    sent_count = 0
    match_dict = dict(match)
    for participant in participants:
        if await send_match_reminder_email(participant, match_dict):
            sent_count += 1
    _flash(request, f"{sent_count}/{len(participants)} rappel(s) match envoyé(s).")
    return RedirectResponse("/admin/communications", status_code=303)


@router.post("/communications/test-smtp")
async def test_smtp(request: Request, email: str = Form(...)):
    await require_admin(request)
    email = email.strip().lower()
    if "@" not in email:
        _flash(request, "Email de test invalide.", "err")
        return RedirectResponse("/admin/communications", status_code=303)
    sent = await send_email(
        email,
        "Test SMTP RESA Pronostics",
        "<p>Le SMTP RESA Pronostics est configuré correctement.</p>",
        "Le SMTP RESA Pronostics est configuré correctement.",
    )
    if sent:
        _flash(request, f"Email de test envoyé à {email}.")
    else:
        _flash(request, "Email de test non envoyé : SMTP absent ou erreur d'envoi.", "err")
    return RedirectResponse("/admin/communications", status_code=303)


# ---- Export ----

@router.get("/export/rankings")
async def export_rankings(request: Request):
    await require_admin(request)
    async with get_db() as db:
        rankings = await get_rankings(db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Rang",
        "Nom",
        "Email",
        "Points totaux",
        "Points pre-tournoi",
        "Scores exacts",
        "Issues correctes",
    ])
    for r in rankings:
        writer.writerow([
            r["rank"],
            r["name"],
            r["email"],
            r["total_points"],
            r["pre_tournament_points"],
            r["exact_count"],
            r["outcome_count"],
        ])
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=classement_resa.csv"},
    )
