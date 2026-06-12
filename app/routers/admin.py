"""Admin backoffice routes."""
import csv
import io
import json
import logging
import uuid

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from app.auth import require_admin, verify_password, hash_password
from app.config import settings
from app.database import get_db
from app.mail import (
    send_invitation,
    send_match_reminder as send_match_reminder_email,
    send_pre_tournament_reminder,
)
from app.nameutils import split_full_name
from app.players import OUTSIDERS, TEAMS_48, get_scorer_options, is_valid_scorer
from app.pre_tournament import (
    DEFAULT_PRE_TOURNAMENT_QUESTIONS,
    get_pre_tournament_deadline,
    get_pre_tournament_question_map,
    get_pre_tournament_questions,
)
from app.scoring import (
    calculate_bonus_scores,
    get_rankings,
    parse_revelation_winners,
    recalculate_match_scores,
    recalculate_pre_tournament_scores,
)
from app.settings_store import (
    KNOCKOUT_OPEN_KEY,
    knockout_predictions_open,
    set_setting,
)
from app.push import push_enabled, send_push_to_participant
from app.templating import create_templates
from app.timeutils import is_match_locked, local_input_to_utc_iso, now_utc_iso

router = APIRouter()
templates = create_templates()
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

BONUS_PHASES = {
    "pre_tournament", "round_of_32", "round_of_16",
    "quarter", "semi", "third_place", "final",
}

PUSH_TEST_DESTINATIONS = {
    "home": ("Accueil", ""),
    "pronos": ("Pronos", "/pronos"),
    "classement": ("Classement", "/classement"),
    "profil": ("Profil", "/profil"),
}

STATUSES = {
    "confirmed": ("ok", "confirmé"),
    "invited": ("warn", "invité"),
    "pending": ("gr", "en attente"),
}


def _now_utc() -> str:
    return now_utc_iso()


def _is_played(match: dict) -> bool:
    return is_match_locked(match)


def _result_from_scores(score_team1: int, score_team2: int) -> str:
    if score_team1 > score_team2:
        return "team1"
    if score_team2 > score_team1:
        return "team2"
    return "draw"


def _qualifier_winner_for_result(
    match: dict,
    score_team1: int,
    score_team2: int,
    qualifier_winner: str,
) -> tuple[str | None, str | None]:
    """Return qualifier winner or an error message for knockout draws."""
    if match["phase"] == "group":
        return None, None
    result = _result_from_scores(score_team1, score_team2)
    if result in ("team1", "team2"):
        return result, None
    if qualifier_winner not in ("team1", "team2"):
        return None, "Choisis l'équipe qualifiée pour ce match de phase finale."
    return qualifier_winner, None


def _flash(request: Request, msg: str, kind: str = "ok"):
    request.session.setdefault("flashes", []).append({"msg": msg, "kind": kind})


def _get_flashes(request: Request):
    return request.session.pop("flashes", [])


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


def _push_test_url(token: str, destination: str) -> str:
    _, suffix = PUSH_TEST_DESTINATIONS.get(destination, PUSH_TEST_DESTINATIONS["home"])
    return f"{settings.BASE_URL.rstrip('/')}/p/{token}{suffix}"


# ---- Login ----

@router.get("/login", response_class=HTMLResponse)
async def admin_login(request: Request):
    if request.session.get("admin_id"):
        return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse(request, "admin/login.html", {
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
               FROM matches
               WHERE result IS NULL
               AND datetime(match_date || 'T' || kickoff_time) >= datetime(?)
               ORDER BY match_date, kickoff_time LIMIT 1""",
            (now,)
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
    return templates.TemplateResponse(request, "admin/dashboard.html", {
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
    return templates.TemplateResponse(request, "admin/participants.html", {
        "request": request,
        "active": "participants",
        "flashes": _get_flashes(request),
        "participants": participants,
        "base_url": request.base_url,
    })


@router.post("/participants/add")
async def add_participant(request: Request, name: str = Form(...), email: str = Form(...)):
    await require_admin(request)
    first_name, last_name = split_full_name(name)
    name = f"{first_name} {last_name}".strip()
    email = email.strip().lower()
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
                _flash(request, f"Participant {name} ajouté, mais l'invitation n'a pas pu être envoyée.", "err")
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
            "SELECT name, email, token FROM participants WHERE id=? AND is_admin=0",
            (participant_id,)
        )
        participant = await row.fetchone()
    if not participant:
        raise HTTPException(404)
    sent = await send_invitation(dict(participant))
    if sent:
        _flash(request, f"Invitation envoyée à {participant['email']}.")
    else:
        _flash(request, f"Impossible d'envoyer l'invitation à {participant['email']}.", "err")
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
        row = await db.execute(
            "SELECT name FROM participants WHERE id=? AND is_admin=0", (participant_id,)
        )
        participant = await row.fetchone()
        if not participant:
            _flash(request, "Participant introuvable.", "err")
            return RedirectResponse("/admin/participants", status_code=303)
        # Hard delete: foreign keys cascade to predictions, scores, bonus answers
        # and pre-tournament data (PRAGMA foreign_keys is ON in get_db).
        await db.execute(
            "DELETE FROM participants WHERE id=? AND is_admin=0", (participant_id,)
        )
        await db.commit()
    _flash(request, f"Participant {participant['name']} supprimé.")
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
        first_name, last_name = split_full_name(row.get("nom", row.get("name", "")))
        name = f"{first_name} {last_name}".strip()
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
            first_name, last_name = split_full_name(name)
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
    _flash(
        request,
        f"{len(imported_participants)} participant(s) importé(s), {sent_count} invitation(s) envoyée(s).",
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
               WHERE is_admin=0
               ORDER BY COALESCE(NULLIF(nickname, ''), name)"""
        )
        participants = [dict(r) for r in await p_rows.fetchall()]
        match_predictions = []
        pre_tournament_rows = []
        bonus_answers = []
        all_matches = []
        pt_questions = await get_pre_tournament_question_map(db, include_disabled=True)

        if view == "matches":
            m_rows = await db.execute(
                """SELECT id, match_number, phase, team1_name, team2_name,
                          match_date, kickoff_time, result
                   FROM matches ORDER BY match_date, kickoff_time"""
            )
            all_matches = [dict(r) for r in await m_rows.fetchall()]

        if view == "matches":
            where = ["p.is_admin=0"]
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
                      pr.qualifier_prediction, pr.admin_entered,
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
            where = ["p.is_admin=0"]
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
                      pt.total_goals, pt.submitted, pt.submitted_at
                    FROM participants p
                    LEFT JOIN pre_tournament_predictions pt
                      ON pt.participant_id = p.id
                    WHERE {' AND '.join(where)}
                    ORDER BY p.name""",
                params,
            )
            pre_tournament_rows = [dict(r) for r in await rows.fetchall()]

        if view == "bonus":
            where = ["p.is_admin=0"]
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

    return templates.TemplateResponse(request, "admin/predictions.html", {
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
        "all_matches": all_matches,
    })


@router.post("/pronostics/force")
async def force_prediction(request: Request,
                           participant_id: int = Form(...),
                           match_id: int = Form(...),
                           score_team1: int = Form(...),
                           score_team2: int = Form(...),
                           qualifier_prediction: str = Form(default="")):
    """Encode un prono au nom d'un participant (ex: reçu par SMS avant le coup
    d'envoi), même si le match est déjà verrouillé."""
    await require_admin(request)
    redirect = RedirectResponse("/admin/pronostics?view=matches", status_code=303)
    if not (0 <= score_team1 <= 30 and 0 <= score_team2 <= 30):
        _flash(request, "Le score doit être compris entre 0 et 30.", "err")
        return redirect
    async with get_db() as db:
        p_row = await db.execute(
            "SELECT * FROM participants WHERE id=? AND is_admin=0", (participant_id,)
        )
        participant = await p_row.fetchone()
        m_row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await m_row.fetchone()
        if not participant or not match:
            _flash(request, "Participant ou match introuvable.", "err")
            return redirect
        match_dict = dict(match)
        prediction = _result_from_scores(score_team1, score_team2)
        qualifier = None
        if match_dict["phase"] != "group" and prediction == "draw":
            if qualifier_prediction not in ("team1", "team2"):
                _flash(request, "Nul en phase finale : indique l'équipe qualifiée.", "err")
                return redirect
            qualifier = qualifier_prediction
        old_row = await db.execute(
            """SELECT exact_score_team1, exact_score_team2 FROM predictions
               WHERE participant_id=? AND match_id=?""",
            (participant_id, match_id),
        )
        old = await old_row.fetchone()
        await db.execute(
            """INSERT INTO predictions (participant_id, match_id, prediction,
                 exact_score_team1, exact_score_team2, qualifier_prediction, admin_entered)
               VALUES (?,?,?,?,?,?,1)
               ON CONFLICT(participant_id, match_id) DO UPDATE SET
                 prediction=excluded.prediction,
                 exact_score_team1=excluded.exact_score_team1,
                 exact_score_team2=excluded.exact_score_team2,
                 qualifier_prediction=excluded.qualifier_prediction,
                 admin_entered=1,
                 submitted_at=datetime('now')""",
            (participant_id, match_id, prediction, score_team1, score_team2, qualifier),
        )
        await db.commit()
        name = participant["nickname"] or participant["name"]
        label = f"{match_dict['team1_name']} – {match_dict['team2_name']}"
        has_result = match_dict.get("result") is not None
    if has_result:
        await recalculate_match_scores(match_id)
    msg = f"Prono {score_team1}-{score_team2} enregistré pour {name} sur {label}."
    if old and old["exact_score_team1"] is not None:
        msg += f" Remplace l'ancien prono {old['exact_score_team1']}-{old['exact_score_team2']}."
    if has_result:
        msg += " Points recalculés."
    _flash(request, msg)
    return redirect


# ---- Pre-tournament Admin ----

@router.get("/pre-tournoi", response_class=HTMLResponse)
async def pre_tournament_admin(request: Request):
    await require_admin(request)
    async with get_db() as db:
        questions = await get_pre_tournament_questions(db, include_disabled=True)
        deadline = await get_pre_tournament_deadline(db)
        submitted_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM pre_tournament_predictions WHERE submitted=1"
        )
        submitted_count = (await submitted_row.fetchone())["cnt"]
        total_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1 AND is_admin=0"
        )
        total_count = (await total_row.fetchone())["cnt"]
        # Per-question hit counts once answers are scored
        hits_rows = await db.execute(
            """SELECT question_key,
                      SUM(CASE WHEN points > 0 THEN 1 ELSE 0 END) as winners,
                      COUNT(*) as scored
               FROM pre_tournament_scores GROUP BY question_key"""
        )
        hits = {r["question_key"]: dict(r) for r in await hits_rows.fetchall()}
    answers = {q["key"]: q.get("correct_answer") or "" for q in questions}
    # The révélation answer is a JSON list of winning outsiders (ties allowed).
    revelation_winners = sorted(parse_revelation_winners(answers.get("revelation")))
    return templates.TemplateResponse(request, "admin/pre_tournament.html", {
        "request": request,
        "active": "pre_tournoi",
        "flashes": _get_flashes(request),
        "questions": questions,
        "deadline": deadline,
        "submitted_count": submitted_count,
        "total_count": total_count,
        "teams": TEAMS_48,
        "outsiders": OUTSIDERS,
        "revelation_winners": revelation_winners,
        "scorer_options": get_scorer_options(),
        "answers": answers,
        "hits": hits,
    })


@router.post("/pre-tournoi/reponses")
async def update_pre_tournament_answers(
    request: Request,
    winner: str = Form(default=""),
    finalist: str = Form(default=""),
    top_scorer: str = Form(default=""),
    revelation: list[str] = Form(default=[]),
    total_goals: str = Form(default=""),
):
    await require_admin(request)
    winner = winner.strip()
    finalist = finalist.strip()
    top_scorer = top_scorer.strip()
    # Several outsiders may win on a tie (same furthest stage reached).
    revelation_winners = [r.strip() for r in revelation if r.strip()]
    total_goals = total_goals.strip()

    if winner and finalist and winner == finalist:
        _flash(request, "Le champion et l'autre finaliste ne peuvent pas être identiques.", "err")
        return RedirectResponse("/admin/pre-tournoi", status_code=303)
    for team_value, label in ((winner, "champion"), (finalist, "autre finaliste")):
        if team_value and team_value not in TEAMS_48:
            _flash(request, f"Équipe inconnue pour {label} : {team_value}.", "err")
            return RedirectResponse("/admin/pre-tournoi", status_code=303)
    for team_value in revelation_winners:
        if team_value not in OUTSIDERS:
            _flash(request, f"Outsider inconnu pour la révélation : {team_value}.", "err")
            return RedirectResponse("/admin/pre-tournoi", status_code=303)
    if top_scorer and not is_valid_scorer(top_scorer):
        _flash(request, "Joueur inconnu pour le meilleur buteur.", "err")
        return RedirectResponse("/admin/pre-tournoi", status_code=303)
    if total_goals:
        try:
            int(total_goals)
        except ValueError:
            _flash(request, "Le total de buts doit être un nombre entier.", "err")
            return RedirectResponse("/admin/pre-tournoi", status_code=303)

    revelation_value = json.dumps(revelation_winners, ensure_ascii=False) if revelation_winners else None
    incoming = {
        "winner": winner or None,
        "finalist": finalist or None,
        "top_scorer": top_scorer or None,
        "revelation": revelation_value,
        "total_goals": total_goals or None,
    }
    async with get_db() as db:
        for key, value in incoming.items():
            await db.execute(
                "UPDATE pre_tournament_questions SET correct_answer=? WHERE key=?",
                (value, key),
            )
        await db.commit()
    await recalculate_pre_tournament_scores()
    _flash(request, "Réponses pré-tournoi enregistrées. Scores recalculés.")
    return RedirectResponse("/admin/pre-tournoi", status_code=303)


@router.post("/pre-tournoi/deadline")
async def update_pre_tournament_deadline(
    request: Request,
    deadline: str = Form(...),
    timezone_name: str = Form(default=""),
):
    await require_admin(request)
    deadline = deadline.strip()
    try:
        deadline_utc = local_input_to_utc_iso(deadline, timezone_name)
    except Exception:
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
    await recalculate_pre_tournament_scores()
    _flash(request, "Question pré-tournoi mise à jour.")
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
        counts = {}
        for ph in PHASE_LABELS:
            c_row = await db.execute("SELECT COUNT(*) as cnt FROM matches WHERE phase=?", (ph,))
            counts[ph] = (await c_row.fetchone())["cnt"]
        knockout_open = await knockout_predictions_open(db)
    return templates.TemplateResponse(request, "admin/matches.html", {
        "request": request,
        "active": "matches",
        "flashes": _get_flashes(request),
        "matches": matches,
        "current_phase": phase,
        "phase_labels": PHASE_LABELS,
        "phase_counts": counts,
        "knockout_open": knockout_open,
    })


@router.post("/matches/knockout-pronos/toggle")
async def toggle_knockout_pronos(request: Request):
    await require_admin(request)
    async with get_db() as db:
        is_open = await knockout_predictions_open(db)
        await set_setting(db, KNOCKOUT_OPEN_KEY, "0" if is_open else "1")
        await db.commit()
    if is_open:
        _flash(request, "Pronostics de phase finale verrouillés.")
    else:
        _flash(request, "Pronostics de phase finale ouverts aux participants.")
    return RedirectResponse("/admin/matches", status_code=303)


@router.post("/matches/{match_id}/toggle-top")
async def toggle_top_match(request: Request, match_id: int):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        if _is_played(dict(match)):
            return JSONResponse({"error": "Match déjà joué"}, status_code=400)
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
    return templates.TemplateResponse(request, "admin/results.html", {
        "request": request,
        "active": "resultats",
        "flashes": _get_flashes(request),
        "pending": pending,
        "done": done,
    })


@router.post("/resultats/{match_id}")
async def encode_result(request: Request, match_id: int,
                        score_team1: int = Form(...), score_team2: int = Form(...),
                        qualifier_winner: str = Form(default="")):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        match_dict = dict(match)
        result = _result_from_scores(score_team1, score_team2)
        qualifier, error = _qualifier_winner_for_result(
            match_dict, score_team1, score_team2, qualifier_winner
        )
        if error:
            _flash(request, error, "err")
            return RedirectResponse("/admin/resultats", status_code=303)
        await db.execute(
            "UPDATE matches SET score_team1=?, score_team2=?, result=?, qualifier_winner=? WHERE id=?",
            (score_team1, score_team2, result, qualifier, match_id)
        )
        await db.commit()
    await recalculate_match_scores(match_id)
    _flash(request, f"Résultat encodé. Scores recalculés.")
    return RedirectResponse("/admin/resultats", status_code=303)


@router.post("/resultats/{match_id}/correct")
async def correct_result(request: Request, match_id: int,
                         score_team1: int = Form(...), score_team2: int = Form(...),
                         qualifier_winner: str = Form(default="")):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        match_dict = dict(match)
        result = _result_from_scores(score_team1, score_team2)
        qualifier, error = _qualifier_winner_for_result(
            match_dict, score_team1, score_team2, qualifier_winner
        )
        if error:
            _flash(request, error, "err")
            return RedirectResponse("/admin/resultats", status_code=303)
        await db.execute(
            "UPDATE matches SET score_team1=?, score_team2=?, result=?, qualifier_winner=? WHERE id=?",
            (score_team1, score_team2, result, qualifier, match_id)
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
                      (SELECT COUNT(*) FROM scores s
                       WHERE s.bonus_question_id = bq.id AND s.points > 0) as correct_count
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
               WHERE p.is_admin=0
               ORDER BY ba.submitted_at DESC"""
        )
        answers_by_question = {}
        for answer in await answer_rows.fetchall():
            answer_dict = dict(answer)
            answers_by_question.setdefault(answer_dict["question_id"], []).append(answer_dict)
        pt_sub_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM pre_tournament_predictions WHERE submitted=1"
        )
        pt_submitted_count = (await pt_sub_row.fetchone())["cnt"]
        pt_total_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1"
        )
        pt_total_count = (await pt_total_row.fetchone())["cnt"]
    return templates.TemplateResponse(request, "admin/bonus.html", {
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
                       deadline: str = Form(...), timezone_name: str = Form(default=""),
                       options_text: str = Form(default=""),
                       correct_answer: str = Form(default="")):
    await require_admin(request)
    # Seul le choix unique est autorisé : les réponses libres (texte/nombre)
    # créent des litiges d'arbitrage (accents, orthographe, formats).
    if answer_type != "choice":
        _flash(request, "Seules les questions à choix unique sont autorisées.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    if phase not in BONUS_PHASES:
        _flash(request, "Phase de question bonus invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    try:
        deadline_utc = local_input_to_utc_iso(deadline, timezone_name)
    except Exception:
        _flash(request, "Deadline invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    options = _normalize_bonus_options(answer_type, options_text)
    if not options or len(json.loads(options)) < 2:
        _flash(request, "Ajoute au moins deux options de réponse.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
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
    points_value: int = Form(...),
    deadline: str = Form(...),
    timezone_name: str = Form(default=""),
    options_text: str = Form(default=""),
    correct_answer: str = Form(default=""),
):
    await require_admin(request)
    if phase not in BONUS_PHASES:
        _flash(request, "Phase de question bonus invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    try:
        deadline_utc = local_input_to_utc_iso(deadline, timezone_name)
    except Exception:
        _flash(request, "Deadline invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    async with get_db() as db:
        row = await db.execute("SELECT answer_type FROM bonus_questions WHERE id=?", (question_id,))
        existing = await row.fetchone()
        if not existing:
            raise HTTPException(404)
        # Le type est figé à la création (choix unique pour les nouvelles questions).
        answer_type = existing["answer_type"]
        options = _normalize_bonus_options(answer_type, options_text)
        if answer_type == "choice" and (not options or len(json.loads(options)) < 2):
            _flash(request, "Ajoute au moins deux options de réponse.", "err")
            return RedirectResponse("/admin/bonus", status_code=303)
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
               WHERE p.is_confirmed=1 AND p.is_admin=0
               AND NOT EXISTS (SELECT 1 FROM pre_tournament_predictions pt
                               WHERE pt.participant_id=p.id AND pt.submitted=1)"""
        )
        no_pt = (await ns_row.fetchone())["cnt"]
        # Upcoming matches
        now = _now_utc()
        m_rows = await db.execute(
            """SELECT * FROM matches
               WHERE result IS NULL
               AND datetime(match_date || 'T' || kickoff_time) >= datetime(?)
               ORDER BY match_date, kickoff_time LIMIT 20""",
            (now,)
        )
        upcoming = [dict(r) for r in await m_rows.fetchall()]
        p_rows = await db.execute(
            """SELECT p.id, p.name, p.email, p.token,
                      COUNT(ps.id) AS subscription_count
               FROM participants p
               LEFT JOIN push_subscriptions ps ON ps.participant_id = p.id
               WHERE p.is_confirmed=1 AND p.is_admin=0
               GROUP BY p.id, p.name, p.email, p.token
               ORDER BY p.name COLLATE NOCASE"""
        )
        push_participants = [dict(r) for r in await p_rows.fetchall()]
    return templates.TemplateResponse(request, "admin/communications.html", {
        "request": request,
        "active": "communications",
        "flashes": _get_flashes(request),
        "no_pt": no_pt,
        "upcoming_matches": upcoming,
        "push_is_enabled": push_enabled(),
        "push_destinations": PUSH_TEST_DESTINATIONS,
        "push_participants": push_participants,
        "push_subscription_count": sum(p["subscription_count"] for p in push_participants),
        "smtp_host": settings.SMTP_HOST,
        "smtp_port": settings.SMTP_PORT,
    })


@router.post("/communications/send-pt-reminder")
async def send_pt_reminder(request: Request):
    await require_admin(request)
    async with get_db() as db:
        rows = await db.execute(
            """SELECT p.name, p.email, p.token FROM participants p
               WHERE p.is_confirmed=1 AND p.is_admin=0 AND p.email_opt_in=1
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
               WHERE p.is_confirmed=1 AND p.is_admin=0 AND p.email_opt_in=1
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


@router.post("/communications/send-push-test")
async def send_push_test(
    request: Request,
    participant_ids: list[int] = Form(default=[]),
    target_mode: str = Form(default="selected"),
    title: str = Form(default=""),
    body: str = Form(default=""),
    destination: str = Form(default="home"),
):
    await require_admin(request)
    if not push_enabled():
        _flash(request, "Notifications push non configurées: clés VAPID manquantes.", "err")
        return RedirectResponse("/admin/communications", status_code=303)

    title = title.strip()[:80]
    body = body.strip()[:180]
    if not title or not body:
        _flash(request, "Titre et message sont obligatoires pour le test push.", "err")
        return RedirectResponse("/admin/communications", status_code=303)

    target_mode = "all" if target_mode == "all" else "selected"
    selected_ids = list(dict.fromkeys(pid for pid in participant_ids if pid > 0))
    if target_mode == "selected" and not selected_ids:
        _flash(request, "Sélectionne au moins un participant.", "err")
        return RedirectResponse("/admin/communications", status_code=303)

    if destination not in PUSH_TEST_DESTINATIONS:
        destination = "home"

    async with get_db() as db:
        if target_mode == "all":
            rows = await db.execute(
                """SELECT id, name, token
                   FROM participants
                   WHERE is_confirmed=1 AND is_admin=0
                   ORDER BY name COLLATE NOCASE"""
            )
        else:
            placeholders = ",".join("?" for _ in selected_ids)
            rows = await db.execute(
                f"""SELECT id, name, token
                    FROM participants
                    WHERE is_confirmed=1 AND is_admin=0
                      AND id IN ({placeholders})
                    ORDER BY name COLLATE NOCASE""",
                selected_ids,
            )
        participants = [dict(r) for r in await rows.fetchall()]

        if not participants:
            _flash(request, "Aucun destinataire valide pour ce test push.", "err")
            return RedirectResponse("/admin/communications", status_code=303)

        sent_count = 0
        for participant in participants:
            delivered = await send_push_to_participant(
                db,
                participant["id"],
                title=title,
                body=body,
                url=_push_test_url(participant["token"], destination),
            )
            if delivered:
                sent_count += 1

    failed_count = len(participants) - sent_count
    _flash(
        request,
        f"{sent_count}/{len(participants)} notification(s) push envoyée(s). "
        f"{failed_count} sans abonnement actif ou en échec.",
        "ok" if sent_count else "err",
    )
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
