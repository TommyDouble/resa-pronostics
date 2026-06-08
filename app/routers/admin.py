"""Admin backoffice routes."""
import csv
import io
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_admin, verify_password, hash_password
from app.database import get_db
from app.scoring import recalculate_match_scores, get_rankings, calculate_bonus_scores

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
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
        total_row = await db.execute("SELECT COUNT(*) as cnt FROM participants WHERE is_admin=0")
        total_participants = (await total_row.fetchone())["cnt"]
        confirmed_row = await db.execute("SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1 AND is_admin=0")
        confirmed = (await confirmed_row.fetchone())["cnt"]
        pt_row = await db.execute("SELECT COUNT(*) as cnt FROM pre_tournament_predictions WHERE submitted=1")
        pt_submitted = (await pt_row.fetchone())["cnt"]
        has_pred_row = await db.execute(
            "SELECT COUNT(DISTINCT participant_id) as cnt FROM predictions"
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
async def participants_list(request: Request):
    await require_admin(request)
    async with get_db() as db:
        rows = await db.execute(
            """SELECT p.*,
                 (SELECT COUNT(*) FROM predictions WHERE participant_id=p.id) as pred_count,
                 (SELECT submitted FROM pre_tournament_predictions WHERE participant_id=p.id) as pt_submitted
               FROM participants p WHERE p.is_admin=0
               ORDER BY p.created_at"""
        )
        participants = [dict(r) for r in await rows.fetchall()]
    return templates.TemplateResponse("admin/participants.html", {
        "request": request,
        "active": "participants",
        "flashes": _get_flashes(request),
        "participants": participants,
        "base_url": request.base_url,
    })


@router.post("/participants/add")
async def add_participant(request: Request, name: str = Form(...), email: str = Form(...)):
    await require_admin(request)
    token = str(uuid.uuid4())
    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO participants (name, email, token) VALUES (?,?,?)",
                (name.strip(), email.strip().lower(), token)
            )
            await db.commit()
            _flash(request, f"Participant {name} ajouté.")
        except Exception as e:
            if "UNIQUE" in str(e):
                _flash(request, "Email déjà utilisé.", "err")
            else:
                _flash(request, "Erreur lors de l'ajout.", "err")
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
        # Check if any predictions exist (can't delete if tournament started)
        pred_row = await db.execute("SELECT COUNT(*) as cnt FROM predictions WHERE participant_id=?", (participant_id,))
        pred_count = (await pred_row.fetchone())["cnt"]
        if pred_count > 0:
            # Soft delete: just mark unconfirmed
            await db.execute("UPDATE participants SET is_confirmed=0 WHERE id=?", (participant_id,))
        else:
            await db.execute("DELETE FROM participants WHERE id=?", (participant_id,))
        await db.commit()
    _flash(request, "Participant supprimé.")
    return RedirectResponse("/admin/participants", status_code=303)


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

    imported = 0
    async with get_db() as db:
        for name, email in valid:
            token = str(uuid.uuid4())
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO participants (name, email, token) VALUES (?,?,?)",
                    (name, email, token)
                )
                imported += 1
            except Exception:
                pass
        await db.commit()
    _flash(request, f"{imported} participant(s) importé(s).")
    return RedirectResponse("/admin/participants", status_code=303)


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
        # Already encoded (last 10)
        done_rows = await db.execute(
            """SELECT * FROM matches WHERE result IS NOT NULL
               ORDER BY match_date DESC, kickoff_time DESC LIMIT 10"""
        )
        done = [dict(r) for r in await done_rows.fetchall()]
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
        rows = await db.execute("SELECT * FROM bonus_questions ORDER BY deadline")
        questions = [dict(r) for r in await rows.fetchall()]
    return templates.TemplateResponse("admin/bonus.html", {
        "request": request,
        "active": "bonus",
        "flashes": _get_flashes(request),
        "questions": questions,
        "phase_labels": PHASE_LABELS,
    })


@router.post("/bonus/create")
async def create_bonus(request: Request,
                       question_text: str = Form(...), phase: str = Form(...),
                       answer_type: str = Form(...), points_value: int = Form(...),
                       deadline: str = Form(...)):
    await require_admin(request)
    async with get_db() as db:
        await db.execute(
            """INSERT INTO bonus_questions (question_text, phase, answer_type, points_value, deadline)
               VALUES (?,?,?,?,?)""",
            (question_text, phase, answer_type, points_value, deadline)
        )
        await db.commit()
    _flash(request, "Question créée.")
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


# ---- Communications ----

@router.get("/communications", response_class=HTMLResponse)
async def communications(request: Request):
    await require_admin(request)
    async with get_db() as db:
        # Count non-submitted pre-tournament
        ns_row = await db.execute(
            """SELECT COUNT(*) as cnt FROM participants p
               WHERE p.is_confirmed=1 AND p.is_admin=0
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
    return templates.TemplateResponse("admin/communications.html", {
        "request": request,
        "active": "communications",
        "flashes": _get_flashes(request),
        "no_pt": no_pt,
        "upcoming_matches": upcoming,
    })


@router.post("/communications/send-pt-reminder")
async def send_pt_reminder(request: Request):
    await require_admin(request)
    # In Phase 1: just log (SMTP optional)
    logger.info("SMTP: pre-tournament reminder sent")
    _flash(request, "Rappel envoyé (ou journalisé si SMTP non configuré).")
    return RedirectResponse("/admin/communications", status_code=303)


@router.post("/communications/send-match-reminder")
async def send_match_reminder(request: Request, match_id: int = Form(...)):
    await require_admin(request)
    logger.info(f"SMTP: match reminder for match {match_id}")
    _flash(request, "Rappel envoyé.")
    return RedirectResponse("/admin/communications", status_code=303)


# ---- Export ----

@router.get("/export/rankings")
async def export_rankings(request: Request):
    await require_admin(request)
    async with get_db() as db:
        rankings = await get_rankings(db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Rang", "Nom", "Email", "Points totaux"])
    for r in rankings:
        writer.writerow([r["rank"], r["name"], r["email"], r["total_points"]])
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=classement_resa.csv"},
    )
