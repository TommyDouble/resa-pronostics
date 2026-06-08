"""Participant-facing HTML page routes."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_participant
from app.database import get_db
from app.scoring import get_rankings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
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

TEAMS_48 = [
    "Mexique","Afrique du Sud","Corée du Sud","Tchéquie",
    "Canada","Bosnie-Herzégovine","Qatar","Suisse",
    "Brésil","Maroc","Haïti","Écosse",
    "États-Unis","Paraguay","Australie","Turquie",
    "Allemagne","Curaçao","Côte d'Ivoire","Équateur",
    "Pays-Bas","Japon","Suède","Tunisie",
    "Belgique","Égypte","Iran","Nouvelle-Zélande",
    "Espagne","Cap-Vert","Arabie Saoudite","Uruguay",
    "France","Sénégal","Irak","Norvège",
    "Argentine","Algérie","Autriche","Jordanie",
    "Portugal","RD Congo","Ouzbékistan","Colombie",
    "Angleterre","Croatie","Ghana","Panama",
]

SCORERS = [
    # Grands favoris au Soulier d'Or
    "Kylian Mbappé", "Harry Kane", "Erling Haaland",
    "Vinícius Júnior", "Lautaro Martínez", "Viktor Gyökeres", "Lamine Yamal",
    # France
    "Marcus Thuram", "Randal Kolo Muani", "Ousmane Dembélé",
    # Angleterre
    "Phil Foden", "Cole Palmer", "Bukayo Saka", "Jarrod Bowen",
    # Brésil
    "Endrick", "Rodrygo", "Matheus Cunha", "Gabriel Martinelli",
    # Espagne
    "Nico Williams", "Álvaro Morata", "Ferran Torres",
    # Allemagne
    "Kai Havertz", "Maximilian Beier", "Leroy Sané",
    # Portugal
    "Cristiano Ronaldo", "Rafael Leão", "Gonçalo Ramos",
    # Pays-Bas
    "Cody Gakpo", "Brian Brobbey",
    # Belgique
    "Romelu Lukaku", "Loïs Openda", "Jérémy Doku",
    # Argentine
    "Julián Álvarez", "Paulo Dybala",
    # Colombie
    "Luis Díaz", "Jhon Durán",
    # Égypte
    "Mohamed Salah",
    # Uruguay
    "Darwin Núñez",
    # Mexique
    "Santiago Giménez", "Raúl Jiménez",
    # Sénégal
    "Ismaïla Sarr", "Sadio Mané",
    # Norvège
    "Alexander Sørloth",
    # Japon
    "Kaoru Mitoma", "Ayase Ueda",
    # Croatie
    "Andrej Kramarić",
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _is_locked(match: dict) -> bool:
    try:
        kickoff = datetime.fromisoformat(f"{match['match_date']}T{match['kickoff_time']}").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= kickoff
    except Exception:
        return False


def _minutes_until(match: dict) -> int:
    try:
        kickoff = datetime.fromisoformat(f"{match['match_date']}T{match['kickoff_time']}").replace(tzinfo=timezone.utc)
        diff = (kickoff - datetime.now(timezone.utc)).total_seconds()
        return int(diff / 60)
    except Exception:
        return 99999


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
        "total_points": total_points,
        "user_rank": user_rank,
        "pending_bonus": pending_bonus,
        "active_nav": active_nav,
        "token": token,
    }


@router.get("/rejoindre", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@router.post("/rejoindre", response_class=HTMLResponse)
async def register_post(request: Request, name: str = Form(...), email: str = Form(...)):
    name = name.strip()
    email = email.strip().lower()
    if not name or not email or "@" not in email:
        return templates.TemplateResponse("register.html", {
            "request": request, "error": "Nom et email valides requis."
        })
    token = str(uuid.uuid4())
    async with get_db() as db:
        # Check if email already registered
        existing = await (await db.execute(
            "SELECT token FROM participants WHERE email=?", (email,)
        )).fetchone()
        if existing:
            return RedirectResponse(url=f"/p/{existing['token']}", status_code=303)
        try:
            await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                (name, email, token)
            )
            await db.commit()
        except Exception:
            return templates.TemplateResponse("register.html", {
                "request": request, "error": "Une erreur est survenue, réessaie."
            })
    return RedirectResponse(url=f"/p/{token}", status_code=303)


@router.get("/p/{token}", response_class=HTMLResponse)
async def participant_home(request: Request, token: str):
    p = await require_participant(token)
    if not p["is_confirmed"]:
        return templates.TemplateResponse("onboarding.html", {
            "request": request, "participant": dict(p), "token": token
        })
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "home")
        # Upcoming/today matches
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = await db.execute(
            """SELECT m.*,
                 p.prediction, p.exact_score_team1, p.exact_score_team2,
                 s.points
               FROM matches m
               LEFT JOIN predictions p ON p.match_id = m.id AND p.participant_id = ?
               LEFT JOIN scores s ON s.match_id = m.id AND s.participant_id = ?
               WHERE m.match_date = ?
               ORDER BY m.kickoff_time""",
            (p["id"], p["id"], today)
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
                m["kickoff_ts"] = int(datetime.fromisoformat(
                    f"{m['match_date']}T{m['kickoff_time']}"
                ).replace(tzinfo=timezone.utc).timestamp())
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
        ctx.update({
            "today_matches": today_matches,
            "urgency": urgency,
            "unpredicted_today": unpredicted_today,
            "mini_rank": mini_rank,
            "encoded_count": encoded_count,
        })
    return templates.TemplateResponse("home.html", {"request": request, **ctx})


@router.post("/p/{token}/confirm", response_class=HTMLResponse)
async def confirm_onboarding(request: Request, token: str,
                              first_name: str = Form(...), last_name: str = Form(...)):
    p = await require_participant(token)
    name = f"{first_name.strip()} {last_name.strip()}"
    async with get_db() as db:
        await db.execute(
            "UPDATE participants SET name = ?, is_confirmed = 1 WHERE token = ?",
            (name, token)
        )
        await db.commit()
    return RedirectResponse(url=f"/p/{token}", status_code=303)


@router.get("/p/{token}/pronos", response_class=HTMLResponse)
async def predictions_page(request: Request, token: str, phase: str = "group"):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "pronos")
        p = ctx["participant"]
        # Get matches for selected phase
        rows = await db.execute(
            """SELECT m.*,
                 pr.prediction, pr.exact_score_team1, pr.exact_score_team2
               FROM matches m
               LEFT JOIN predictions pr ON pr.match_id = m.id AND pr.participant_id = ?
               ORDER BY m.match_date, m.kickoff_time""",
            (p["id"],)
        )
        all_matches = [dict(r) for r in await rows.fetchall()]
        for m in all_matches:
            m["is_locked"] = _is_locked(m)
            m["phase_label"] = PHASE_LABELS.get(m["phase"], m["phase"])
        # Group by phase then by date
        phases_order = ["group", "round_of_32", "quarter", "semi", "third_place", "final"]
        # Pre-tournament warning
        pt_row = await db.execute(
            "SELECT submitted FROM pre_tournament_predictions WHERE participant_id = ?", (p["id"],)
        )
        pt = await pt_row.fetchone()
        pt_submitted = pt and pt["submitted"]
        ctx.update({
            "matches": all_matches,
            "current_phase": phase,
            "phases": phases_order,
            "phase_labels": PHASE_LABELS,
            "pt_submitted": pt_submitted,
        })
    return templates.TemplateResponse("predictions.html", {"request": request, **ctx})


@router.get("/p/{token}/pre-tournoi", response_class=HTMLResponse)
async def pre_tournament_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "pronos")
        p = ctx["participant"]
        row = await db.execute(
            "SELECT * FROM pre_tournament_predictions WHERE participant_id = ?", (p["id"],)
        )
        pt = await row.fetchone()
        # Outsiders list (hardcoded for now — admin can customize later)
        outsiders = ["Maroc", "Japon", "USA", "Sénégal", "Australie", "Iran", "Côte d'Ivoire", "Équateur"]
        ctx.update({
            "pt": dict(pt) if pt else {},
            "teams": TEAMS_48,
            "scorers": SCORERS,
            "outsiders": outsiders,
        })
    return templates.TemplateResponse("pre_tournament.html", {"request": request, **ctx})


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
    async with get_db() as db:
        existing = await (await db.execute(
            "SELECT id, submitted FROM pre_tournament_predictions WHERE participant_id = ?", (p["id"],)
        )).fetchone()
        if existing and existing["submitted"]:
            # Already submitted, can't modify
            return RedirectResponse(url=f"/p/{token}/pre-tournoi", status_code=303)
        submitted = 1 if action == "submit" else 0
        submitted_at = _now_utc() if submitted else None
        if existing:
            await db.execute(
                """UPDATE pre_tournament_predictions
                   SET winner=?, finalist=?, top_scorer=?, revelation=?, total_goals=?,
                       submitted=?, submitted_at=?
                   WHERE participant_id=?""",
                (winner, finalist, top_scorer, revelation, total_goals,
                 submitted, submitted_at, p["id"])
            )
        else:
            await db.execute(
                """INSERT INTO pre_tournament_predictions
                   (participant_id, winner, finalist, top_scorer, revelation, total_goals, submitted, submitted_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (p["id"], winner, finalist, top_scorer, revelation, total_goals, submitted, submitted_at)
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
    return templates.TemplateResponse("ranking.html", {"request": request, **ctx})


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
            """SELECT par.name, pr.prediction, pr.exact_score_team1, pr.exact_score_team2, s.points
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
    return templates.TemplateResponse("match_detail.html", {"request": request, **ctx})


@router.get("/p/{token}/profil", response_class=HTMLResponse)
async def own_profile(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "profil")
        p = ctx["participant"]
        profile_data = await _build_profile(p["id"], db)
        ctx.update({"profile": profile_data, "is_own": True})
    return templates.TemplateResponse("profile.html", {"request": request, **ctx})


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
    return templates.TemplateResponse("profile.html", {"request": request, **ctx})


async def _build_profile(participant_id: int, db, viewer_id: int = None) -> dict:
    """Build profile data for a participant."""
    row = await db.execute("SELECT * FROM participants WHERE id=?", (participant_id,))
    p = dict(await row.fetchone())
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
    initials = "".join(w[0].upper() for w in p["name"].split()[:2]) if p["name"] else "??"
    return {
        "participant": p,
        "name": p["name"],
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
        "last5": last5,
        "comparison": comparison,
    }


@router.get("/p/{token}/bonus", response_class=HTMLResponse)
async def bonus_page(request: Request, token: str):
    async with get_db() as db:
        ctx = await _get_participant_context(token, db, "bonus")
        p = ctx["participant"]
        now = _now_utc()
        # Current pending question
        q_row = await db.execute(
            """SELECT bq.* FROM bonus_questions bq
               WHERE bq.deadline > ? AND NOT EXISTS (
                 SELECT 1 FROM bonus_answers ba WHERE ba.question_id=bq.id AND ba.participant_id=?
               )
               ORDER BY bq.deadline LIMIT 1""",
            (now, p["id"])
        )
        current_q = await q_row.fetchone()
        # Past answered questions
        hist_rows = await db.execute(
            """SELECT bq.question_text, bq.phase, bq.correct_answer, bq.points_value,
                      ba.answer, COALESCE(s.points, 0) as points
               FROM bonus_answers ba
               JOIN bonus_questions bq ON bq.id = ba.question_id
               LEFT JOIN scores s ON s.bonus_question_id = bq.id AND s.participant_id = ba.participant_id
               WHERE ba.participant_id=?
               ORDER BY ba.submitted_at DESC""",
            (p["id"],)
        )
        history = [dict(r) for r in await hist_rows.fetchall()]
        ctx.update({
            "current_q": dict(current_q) if current_q else None,
            "history": history,
        })
    return templates.TemplateResponse("bonus.html", {"request": request, **ctx})


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
