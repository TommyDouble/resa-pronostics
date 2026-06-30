"""Admin backoffice routes."""
import csv
import io
import json
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from app.auth import require_admin, verify_password, hash_password
from app.config import settings
from app.database import get_db
from app.knockout import (
    confirm_match_side,
    confirm_match_teams,
    enrich_knockout_matches,
    match_predictions_open,
    propagate_from_match,
    set_match_predictions_open,
)
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
    closest_bonus_standings,
    format_number_multi,
    format_team_list,
    get_rankings,
    normalize_closest_config,
    normalize_number_multi_config,
    parse_bonus_number,
    parse_number_multi,
    parse_revelation_winners,
    parse_team_set,
    recalculate_match_scores,
    recalculate_pre_tournament_scores,
    serialize_closest_config,
)
from app.push import push_enabled, send_push_to_participant
from app.templating import create_templates
from app.timeutils import (
    DISPLAY_TZ,
    current_sporting_day,
    format_match_local_date,
    format_sporting_day_fr,
    is_match_locked,
    local_input_to_utc_iso,
    match_kickoff_utc,
    match_live_state,
    now_utc_iso,
    parse_utc_iso,
    sporting_day_bounds,
    utc_iso,
)

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
    "pre_tournament", "group", "round_of_32", "round_of_16",
    "quarter", "semi", "third_place", "final",
}

PUSH_TEST_DESTINATIONS = {
    "home": ("Accueil", ""),
    "pronos": ("Pronos", "/pronos"),
    "classement": ("Classement", "/classement"),
    "profil": ("Profil", "/profil"),
}

DEADLINE_REMINDER_KIND = "admin_deadline_reminder"

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


def _parse_optional_score(value) -> tuple[int | None, str | None]:
    raw = "" if value is None else str(value).strip()
    if raw == "":
        return None, None
    try:
        score = int(raw)
    except ValueError:
        return None, "Score final invalide."
    if not 0 <= score <= 30:
        return None, "Les scores doivent être compris entre 0 et 30."
    return score, None


def _match_result_payload(
    match: dict,
    score_team1: int,
    score_team2: int,
    final_score_team1,
    final_score_team2,
    qualifier_winner: str,
) -> tuple[dict | None, str | None]:
    if not (0 <= score_team1 <= 30 and 0 <= score_team2 <= 30):
        return None, "Les scores doivent être compris entre 0 et 30."

    result = _result_from_scores(score_team1, score_team2)
    if match["phase"] == "group":
        return {
            "score_team1": score_team1,
            "score_team2": score_team2,
            "final_score_team1": score_team1,
            "final_score_team2": score_team2,
            "result": result,
            "qualifier_winner": None,
        }, None

    if result in ("team1", "team2"):
        return {
            "score_team1": score_team1,
            "score_team2": score_team2,
            "final_score_team1": score_team1,
            "final_score_team2": score_team2,
            "result": result,
            "qualifier_winner": result,
        }, None

    final1, error = _parse_optional_score(final_score_team1)
    if error:
        return None, error
    final2, error = _parse_optional_score(final_score_team2)
    if error:
        return None, error
    if (final1 is None) != (final2 is None):
        return None, "Renseigne les deux scores finaux après prolongation, ou laisse les deux vides."
    if final1 is None and final2 is None:
        final1, final2 = score_team1, score_team2

    if final1 < score_team1 or final2 < score_team2:
        return None, "Le score final ne peut pas être inférieur au score à 90 minutes."

    final_result = _result_from_scores(final1, final2)
    if final_result in ("team1", "team2"):
        qualifier = final_result
    elif qualifier_winner in ("team1", "team2"):
        qualifier = qualifier_winner
    else:
        return None, "Choisis l'équipe qualifiée pour ce match de phase finale."

    return {
        "score_team1": score_team1,
        "score_team2": score_team2,
        "final_score_team1": final1,
        "final_score_team2": final2,
        "result": result,
        "qualifier_winner": qualifier,
    }, None


def _flash(request: Request, msg: str, kind: str = "ok"):
    request.session.setdefault("flashes", []).append({"msg": msg, "kind": kind})


def _get_flashes(request: Request):
    return request.session.pop("flashes", [])


def _compose_question_text(title: str, prompt: str) -> str:
    """Recompose l'intitulé stocké à partir des deux champs admin.

    Le rendu participant (et l'aperçu admin) découpe sur le premier « — »
    pour afficher un titre court manuscrit + la question en police normale.
    On rejoint donc avec ce même séparateur. Titre optionnel."""
    title = (title or "").strip()
    prompt = (prompt or "").strip()
    if title and prompt:
        return f"{title} — {prompt}"
    return prompt or title


def _normalize_bonus_options(answer_type: str, options_text: str):
    if answer_type not in ("choice", "multi_choice", "number_multi"):
        return None
    options = [
        opt.strip()
        for line in options_text.splitlines()
        for opt in line.split(",")
        if opt.strip()
    ]
    return json.dumps(options, ensure_ascii=False) if options else None


def _bonus_int(value, default: int = 0) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return default


def _closest_form_config(
    answer_type: str,
    points_value: int,
    preset_key: str,
    award_mode: str,
    tie_policy: str,
    rank1_points: int,
    rank2_points: int,
    rank3_points: int,
) -> tuple[int, str | None]:
    if answer_type != "number":
        return points_value, None
    presets = {
        "fun_balanced": ("podium_custom", "full_skip", [6, 4, 2]),
        "winner_takes_all": ("winner_takes_all", "full_skip", [6, 0, 0]),
        "top2": ("podium_custom", "full_skip", [6, 3, 0]),
    }
    if preset_key in presets:
        award_mode, tie_policy, rank_points = presets[preset_key]
    else:
        preset_key = "custom"
        rank_points = [
            _bonus_int(rank1_points, points_value),
            _bonus_int(rank2_points, max(points_value - 2, 0)),
            _bonus_int(rank3_points, max(points_value - 4, 0)),
        ]
    rank_points = [
        _bonus_int(rank_points[0], points_value),
        _bonus_int(rank_points[1], max(points_value - 2, 0)),
        _bonus_int(rank_points[2], max(points_value - 4, 0)),
    ]
    if award_mode == "winner_takes_all":
        rank_points = [rank_points[0], 0, 0]
    else:
        award_mode = "podium_custom"
    if tie_policy not in {"full_skip", "full_dense", "share_occupied"}:
        tie_policy = "full_skip"
    return rank_points[0], serialize_closest_config(
        rank_points[0], award_mode, tie_policy, rank_points, preset_key
    )


def _ordered_team_list(options: list[str], selected: set[str]) -> list[str]:
    ordered = [opt for opt in options if opt in selected]
    for team in sorted(selected - set(ordered)):
        ordered.append(team)
    return ordered


def _number_multi_scoring_config(points_value: int, raw_config=None, options=None) -> str:
    config = normalize_number_multi_config(points_value, raw_config, options)
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def _preserve_closest_value_bounds(scoring_config: str | None, existing_config: str | None) -> str | None:
    if not scoring_config or not existing_config:
        return scoring_config
    try:
        new_config = json.loads(scoring_config)
        old_config = json.loads(existing_config)
    except (TypeError, ValueError):
        return scoring_config
    if not isinstance(new_config, dict) or not isinstance(old_config, dict):
        return scoring_config
    for key in ("min_value", "max_value"):
        if key in old_config and key not in new_config:
            new_config[key] = old_config[key]
    return json.dumps(new_config, ensure_ascii=False, separators=(",", ":"))


def _number_multi_correct_from_form(form, options: list[str], scoring_config: str):
    config = normalize_number_multi_config(0, scoring_config, options)
    valid_options = set(options)
    locked = set(config["locked_teams"])
    selected = {
        v for v in form.getlist("correct_answer")
        if v in valid_options and v not in locked
    }
    raw_count = (form.get("correct_count") or "").strip()
    if not raw_count and not selected:
        return "", None
    try:
        count_val = int(raw_count)
    except ValueError:
        return None, "Le total correct doit être un nombre entier."
    if count_val < config["min_count"] or count_val > config["max_count"]:
        return None, f"Le total correct doit être entre {config['min_count']} et {config['max_count']}."
    teams = locked | selected
    if len(teams) != count_val:
        return None, f"Coche exactement {count_val} équipe(s), équipes déjà validées incluses."
    return json.dumps(
        {"count": count_val, "teams": _ordered_team_list(options, teams)},
        ensure_ascii=False,
    ), None


def _push_test_url(token: str, destination: str) -> str:
    _, suffix = PUSH_TEST_DESTINATIONS.get(destination, PUSH_TEST_DESTINATIONS["home"])
    return f"{settings.BASE_URL.rstrip('/')}/p/{token}{suffix}"


def _display_deadline_label(deadline: str) -> str:
    try:
        return parse_utc_iso(deadline).astimezone(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return deadline or ""


def _normalize_deadline_iso(deadline: str) -> str:
    try:
        return utc_iso(parse_utc_iso(deadline))
    except Exception:
        return (deadline or "").strip()


def _admin_deadline_reminder_ref(deadline: str, reminder_day: str | None = None) -> str:
    if reminder_day is None:
        reminder_day = parse_utc_iso(_now_utc()).astimezone(DISPLAY_TZ).strftime("%Y-%m-%d")
    return f"{reminder_day}|{_normalize_deadline_iso(deadline)}"


def _short_text(value: str, limit: int = 54) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _bonus_short_label(question_text: str) -> str:
    text = question_text or "Question bonus"
    if " — " in text:
        return text.split(" — ", 1)[0].strip() or text
    if " - " in text:
        return text.split(" - ", 1)[0].strip() or text
    return text


def _deadline_push_title(count: int) -> str:
    suffix = "info" if count == 1 else "infos"
    return f"RESA Pronostics - {count} {suffix} à encoder"


def _deadline_push_body(missing_items: list[dict]) -> str:
    labels = [_short_text(item["label"], 42) for item in missing_items[:3]]
    body = "À compléter: " + ", ".join(labels)
    if len(missing_items) > 3:
        body += f" +{len(missing_items) - 3}"
    return body[:180]


async def _deadline_participants(db) -> list[dict]:
    rows = await db.execute(
        """SELECT p.id, p.name, p.nickname, p.email, p.token,
                  COUNT(ps.id) AS subscription_count
           FROM participants p
           LEFT JOIN push_subscriptions ps ON ps.participant_id = p.id
           WHERE p.is_confirmed=1 AND p.is_admin=0
           GROUP BY p.id, p.name, p.nickname, p.email, p.token
           ORDER BY COALESCE(NULLIF(p.nickname, ''), p.name) COLLATE NOCASE"""
    )
    participants = []
    for row in await rows.fetchall():
        participant = dict(row)
        participant["display_name"] = participant.get("nickname") or participant.get("name")
        participant["subscription_count"] = participant.get("subscription_count") or 0
        participants.append(participant)
    return participants


async def _answered_ids(db, table: str, ref_column: str | None = None, ref_id: int | None = None) -> set[int]:
    if table == "pre_tournament_predictions":
        rows = await db.execute(
            """SELECT pt.participant_id
               FROM pre_tournament_predictions pt
               JOIN participants p ON p.id=pt.participant_id
               WHERE p.is_confirmed=1 AND p.is_admin=0 AND pt.submitted=1"""
        )
    elif table == "predictions":
        rows = await db.execute(
            """SELECT pr.participant_id
               FROM predictions pr
               JOIN participants p ON p.id=pr.participant_id
               WHERE p.is_confirmed=1 AND p.is_admin=0 AND pr.match_id=?""",
            (ref_id,),
        )
    elif table == "bonus_answers":
        rows = await db.execute(
            """SELECT ba.participant_id
               FROM bonus_answers ba
               JOIN participants p ON p.id=ba.participant_id
               WHERE p.is_confirmed=1 AND p.is_admin=0 AND ba.question_id=?""",
            (ref_id,),
        )
    else:
        return set()
    return {row["participant_id"] for row in await rows.fetchall()}


def _empty_deadline_group(deadline: str) -> dict:
    deadline = _normalize_deadline_iso(deadline)
    return {
        "key": deadline,
        "deadline": deadline,
        "deadline_label": _display_deadline_label(deadline),
        "events": [],
    }


def _finalize_deadline_groups(groups: dict, participants: list[dict], now: str) -> list[dict]:
    participant_total = len(participants)
    finalized = []
    for deadline, group in groups.items():
        missing_participants = []
        for participant in participants:
            missing_events = [
                event for event in group["events"]
                if participant["id"] not in event["answered_ids"]
            ]
            if not missing_events:
                continue
            missing_participants.append({
                "id": participant["id"],
                "name": participant["display_name"],
                "token": participant["token"],
                "subscription_count": participant["subscription_count"],
                "missing": [
                    {
                        "label": event["label"],
                        "kind": event["kind"],
                        "event_id": event["event_id"],
                    }
                    for event in missing_events
                ],
            })
        for event in group["events"]:
            event.pop("answered_ids", None)
        group["participant_total"] = participant_total
        group["missing_participants"] = missing_participants
        group["missing_count"] = len(missing_participants)
        group["answered_count"] = max(participant_total - group["missing_count"], 0)
        group["push_reachable_count"] = sum(
            1 for item in missing_participants if item["subscription_count"] > 0
        )
        group["event_count"] = len(group["events"])
        group["is_future"] = deadline > now
        group["selectable"] = group["is_future"] and group["missing_count"] > 0
        group["event_summary"] = " · ".join(event["label"] for event in group["events"][:3])
        if len(group["events"]) > 3:
            group["event_summary"] += f" · +{len(group['events']) - 3}"
        finalized.append(group)
    return finalized


async def _build_deadline_groups(db, *, future_limit: int | None = 10) -> dict:
    now = _now_utc()
    participants = await _deadline_participants(db)
    groups: dict[str, dict] = {}

    def add_event(deadline: str, event: dict):
        deadline = _normalize_deadline_iso(deadline)
        if not deadline:
            return
        group = groups.setdefault(deadline, _empty_deadline_group(deadline))
        group["events"].append(event)

    pt_deadline = _normalize_deadline_iso(await get_pre_tournament_deadline(db))
    add_event(pt_deadline, {
        "kind": "pre_tournament",
        "event_id": "pre_tournament",
        "label": "Pré-tournoi",
        "answered_ids": await _answered_ids(db, "pre_tournament_predictions"),
    })

    match_rows = await db.execute(
        """SELECT *
           FROM matches
           WHERE result IS NULL
           ORDER BY match_date, kickoff_time, match_number"""
    )
    for row in await match_rows.fetchall():
        match = dict(row)
        if not match_predictions_open(match):
            continue
        deadline = utc_iso(match_kickoff_utc(match))
        add_event(deadline, {
            "kind": "match",
            "event_id": match["id"],
            "label": f"#{match['match_number']} · {match['team1_name']} – {match['team2_name']}",
            "answered_ids": await _answered_ids(db, "predictions", "match_id", match["id"]),
        })

    bonus_rows = await db.execute(
        """SELECT id, question_text, deadline
           FROM bonus_questions
           WHERE is_published=1
           ORDER BY deadline, id"""
    )
    for row in await bonus_rows.fetchall():
        question = dict(row)
        add_event(question["deadline"], {
            "kind": "bonus",
            "event_id": question["id"],
            "label": f"Bonus · {_short_text(_bonus_short_label(question['question_text']), 46)}",
            "answered_ids": await _answered_ids(db, "bonus_answers", "question_id", question["id"]),
        })

    all_groups = _finalize_deadline_groups(groups, participants, now)
    late_groups = sorted(
        [group for group in all_groups if not group["is_future"] and group["missing_count"] > 0],
        key=lambda item: item["deadline"],
        reverse=True,
    )
    future_groups = sorted(
        [group for group in all_groups if group["is_future"]],
        key=lambda item: item["deadline"],
    )
    if future_limit is not None:
        future_groups = future_groups[:future_limit]
    return {
        "participant_total": len(participants),
        "late_groups": late_groups,
        "future_groups": future_groups,
        "all_groups": sorted(all_groups, key=lambda item: item["deadline"]),
    }


async def _build_deadline_preview(db, deadline_keys: list[str]) -> dict:
    selected = {_normalize_deadline_iso(key) for key in deadline_keys if key}
    deadline_data = await _build_deadline_groups(db, future_limit=None)
    groups = [
        group for group in deadline_data["all_groups"]
        if group["key"] in selected and group["selectable"]
    ]
    participant_map: dict[int, dict] = {}
    for group in groups:
        for missing in group["missing_participants"]:
            participant = participant_map.setdefault(missing["id"], {
                "id": missing["id"],
                "name": missing["name"],
                "token": missing["token"],
                "subscription_count": missing["subscription_count"],
                "deadline_keys": set(),
                "missing": [],
                "today_reminder_refs": set(),
            })
            participant["deadline_keys"].add(group["key"])
            for item in missing["missing"]:
                participant["missing"].append({
                    **item,
                    "deadline": group["deadline"],
                    "deadline_label": group["deadline_label"],
                })

    today = parse_utc_iso(_now_utc()).astimezone(DISPLAY_TZ).strftime("%Y-%m-%d")
    refs = [_admin_deadline_reminder_ref(group["key"], today) for group in groups]
    if participant_map and refs:
        placeholders_refs = ",".join("?" for _ in refs)
        placeholders_ids = ",".join("?" for _ in participant_map)
        rows = await db.execute(
            f"""SELECT participant_id, ref
                FROM notification_log
                WHERE kind=?
                  AND ref IN ({placeholders_refs})
                  AND participant_id IN ({placeholders_ids})""",
            (DEADLINE_REMINDER_KIND, *refs, *participant_map.keys()),
        )
        for row in await rows.fetchall():
            participant = participant_map.get(row["participant_id"])
            if participant:
                participant["today_reminder_refs"].add(row["ref"])

    participants = []
    for participant in participant_map.values():
        participant["deadline_keys"] = sorted(participant["deadline_keys"])
        participant["missing_count"] = len(participant["missing"])
        participant["has_today_reminder"] = bool(participant["today_reminder_refs"])
        participant["today_reminder_count"] = len(participant["today_reminder_refs"])
        participant["today_reminder_refs"] = sorted(participant["today_reminder_refs"])
        participant["missing_preview"] = ", ".join(
            _short_text(item["label"], 42) for item in participant["missing"][:4]
        )
        if participant["missing_count"] > 4:
            participant["missing_preview"] += f" +{participant['missing_count'] - 4}"
        participants.append(participant)
    participants.sort(key=lambda item: item["name"].lower())

    reachable = [item for item in participants if item["subscription_count"] > 0]
    return {
        "groups": groups,
        "selected_deadline_keys": [group["key"] for group in groups],
        "participants": participants,
        "participant_count": len(participants),
        "reachable_count": len(reachable),
        "unreachable_count": len(participants) - len(reachable),
        "already_reminded_count": sum(1 for item in participants if item["has_today_reminder"]),
        "missing_item_count": sum(item["missing_count"] for item in participants),
    }


async def _communications_context(request: Request, db, *, deadline_preview: dict | None = None) -> dict:
    ns_row = await db.execute(
        """SELECT COUNT(*) as cnt FROM participants p
           WHERE p.is_confirmed=1 AND p.is_admin=0
           AND NOT EXISTS (SELECT 1 FROM pre_tournament_predictions pt
                           WHERE pt.participant_id=p.id AND pt.submitted=1)"""
    )
    no_pt = (await ns_row.fetchone())["cnt"]
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
    deadline_data = await _build_deadline_groups(db)
    return {
        "request": request,
        "active": "communications",
        "flashes": _get_flashes(request),
        "no_pt": no_pt,
        "upcoming_matches": upcoming,
        "deadline_late_groups": deadline_data["late_groups"],
        "deadline_future_groups": deadline_data["future_groups"],
        "deadline_participant_total": deadline_data["participant_total"],
        "deadline_preview": deadline_preview,
        "push_is_enabled": push_enabled(),
        "push_destinations": PUSH_TEST_DESTINATIONS,
        "push_participants": push_participants,
        "push_subscription_count": sum(p["subscription_count"] for p in push_participants),
        "smtp_host": settings.SMTP_HOST,
        "smtp_port": settings.SMTP_PORT,
    }


def _bracket_flash_suffix(propagation: dict) -> str:
    updates = sorted({u["match_number"] for u in propagation.get("updates", [])})
    conflicts = sorted({c["match_number"] for c in propagation.get("conflicts", [])})
    parts = []
    if updates:
        parts.append("Affiches mises à jour: " + ", ".join(f"#{n}" for n in updates) + ".")
    if conflicts:
        parts.append(
            "À revalider: "
            + ", ".join(f"#{n}" for n in conflicts)
            + " fermé(s), pronos conservés."
        )
    return " " + " ".join(parts) if parts else ""


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

        # ---- Section « À faire » ----
        dashboard_sporting_day = current_sporting_day()
        day_start, day_end = sporting_day_bounds(dashboard_sporting_day)
        today_rows = await db.execute(
            """SELECT * FROM matches
               WHERE datetime(match_date || 'T' || kickoff_time) >= datetime(?)
                 AND datetime(match_date || 'T' || kickoff_time) < datetime(?)
               ORDER BY match_date, kickoff_time""",
            (day_start, day_end),
        )
        today_matches = [dict(r) for r in await today_rows.fetchall()]
        for m in today_matches:
            m["live_state"] = match_live_state(m)
            calendar_day = format_match_local_date(m)
            m["is_overnight"] = calendar_day != dashboard_sporting_day
            m["calendar_day_label"] = format_sporting_day_fr(calendar_day).split(" ", 1)[0]

        alert_row = await db.execute(
            """SELECT COUNT(*) as cnt FROM matches
               WHERE result IS NULL
               AND datetime(match_date || 'T' || kickoff_time) < datetime(?, '-2 hours')""",
            (now,)
        )
        late_matches = (await alert_row.fetchone())["cnt"]

        confirmed_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1 AND is_admin=0"
        )
        confirmed = (await confirmed_row.fetchone())["cnt"]

        # Prochain match : taux de participation + retardataires.
        next_row = await db.execute(
            """SELECT * FROM matches
               WHERE result IS NULL
               AND (phase='group' OR predictions_open=1)
               AND datetime(match_date || 'T' || kickoff_time) >= datetime(?)
               ORDER BY match_date, kickoff_time LIMIT 1""",
            (now,)
        )
        next_match = await next_row.fetchone()
        next_match = dict(next_match) if next_match else None
        next_pred_count = 0
        missing_players = []
        if next_match:
            cnt_row = await db.execute(
                "SELECT COUNT(*) as cnt FROM predictions WHERE match_id=?",
                (next_match["id"],),
            )
            next_pred_count = (await cnt_row.fetchone())["cnt"]
            miss_rows = await db.execute(
                """SELECT COALESCE(NULLIF(p.nickname, ''), p.name) AS name
                   FROM participants p
                   WHERE p.is_confirmed=1 AND p.is_admin=0
                     AND NOT EXISTS (SELECT 1 FROM predictions pr
                                     WHERE pr.participant_id=p.id AND pr.match_id=?)
                   ORDER BY name""",
                (next_match["id"],),
            )
            missing_players = [r["name"] for r in await miss_rows.fetchall()]

        unpaid_rows = await db.execute(
            """SELECT COALESCE(NULLIF(nickname, ''), name) AS name
               FROM participants
               WHERE is_confirmed=1 AND is_admin=0 AND has_paid=0
               ORDER BY name"""
        )
        unpaid = [r["name"] for r in await unpaid_rows.fetchall()]

        bonus_close_rows = await db.execute(
            """SELECT id, question_text,
                 (SELECT COUNT(*) FROM bonus_answers WHERE question_id=bonus_questions.id) AS answers
               FROM bonus_questions
               WHERE is_published=1 AND deadline <= ? AND correct_answer IS NULL
               ORDER BY deadline""",
            (now,),
        )
        bonus_to_close = [dict(r) for r in await bonus_close_rows.fetchall()]
        bonus_soon_rows = await db.execute(
            """SELECT id, question_text, deadline FROM bonus_questions
               WHERE is_published=1
                 AND deadline > ? AND deadline <= datetime(?, '+48 hours')
               ORDER BY deadline""",
            (now, now),
        )
        bonus_closing_soon = [dict(r) for r in await bonus_soon_rows.fetchall()]

        # ---- Section « Santé du jeu » ----
        total_row = await db.execute("SELECT COUNT(*) as cnt FROM participants WHERE is_admin=0")
        total_participants = (await total_row.fetchone())["cnt"]
        paid_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1 AND is_admin=0 AND has_paid=1"
        )
        paid = (await paid_row.fetchone())["cnt"]
        preds_today_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM predictions WHERE submitted_at >= ? AND submitted_at < ?",
            (day_start, day_end),
        )
        preds_today = (await preds_today_row.fetchone())["cnt"]
        total_preds_row = await db.execute("SELECT COUNT(*) as cnt FROM predictions")
        total_preds = (await total_preds_row.fetchone())["cnt"]

        # Activité : pronos soumis par jour sur les 7 derniers jours.
        act_rows = await db.execute(
            """SELECT date(submitted_at) AS day, COUNT(*) AS cnt
               FROM predictions
               WHERE submitted_at >= datetime(?, '-6 days', 'start of day')
               GROUP BY day""",
            (now,),
        )
        act_by_day = {r["day"]: r["cnt"] for r in await act_rows.fetchall()}
        activity = []
        today = datetime.fromisoformat(now).date()
        for offset in range(6, -1, -1):
            day = today - timedelta(days=offset)
            cnt = act_by_day.get(day.isoformat(), 0)
            activity.append({"label": day.strftime("%d/%m"), "cnt": cnt})
        act_max = max((a["cnt"] for a in activity), default=0) or 1
        for a in activity:
            a["height"] = max(8, round(a["cnt"] / act_max * 100))

        podium = (await get_rankings(db))[:3]

        # 5 derniers matchs joués : score + réussite de la communauté.
        last_enc = await db.execute(
            """SELECT m.match_number, m.team1_name, m.team2_name,
                      m.phase, m.score_team1, m.score_team2,
                      m.final_score_team1, m.final_score_team2,
                      m.result, m.qualifier_winner,
                      m.match_date, m.kickoff_time,
                      (SELECT COUNT(*) FROM scores s WHERE s.match_id=m.id) AS scored,
                      (SELECT COUNT(*) FROM scores s WHERE s.match_id=m.id AND s.points > 0) AS correct,
                      (SELECT ROUND(AVG(s.points), 1) FROM scores s WHERE s.match_id=m.id) AS avg_pts
               FROM matches m WHERE m.result IS NOT NULL
               ORDER BY m.match_date DESC, m.kickoff_time DESC LIMIT 5"""
        )
        last_encoded = [dict(r) for r in await last_enc.fetchall()]
        for m in last_encoded:
            m["pct_correct"] = round(m["correct"] / m["scored"] * 100) if m["scored"] else 0

    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "request": request,
        "active": "dashboard",
        "flashes": _get_flashes(request),
        "today_matches": today_matches,
        "sporting_day_label": format_sporting_day_fr(dashboard_sporting_day),
        "sporting_day_help": (
            f"Du {format_sporting_day_fr(dashboard_sporting_day)} à 9 h au "
            f"{format_sporting_day_fr((datetime.fromisoformat(dashboard_sporting_day).date() + timedelta(days=1)).isoformat())} à 8 h 59"
        ),
        "late_matches": late_matches,
        "next_match": next_match,
        "next_pred_count": next_pred_count,
        "missing_players": missing_players,
        "confirmed": confirmed,
        "unpaid": unpaid,
        "bonus_to_close": bonus_to_close,
        "bonus_closing_soon": bonus_closing_soon,
        "total_participants": total_participants,
        "paid": paid,
        "preds_today": preds_today,
        "total_preds": total_preds,
        "activity": activity,
        "podium": podium,
        "last_encoded": last_encoded,
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
    # Appelé en fetch depuis la table participants : la ligne est mise à jour
    # sur place, sans rechargement (préserve scroll, recherche et tri).
    return JSONResponse({"has_paid": new_val})


@router.post("/participants/{participant_id}/toggle-favorite")
async def toggle_favorite(request: Request, participant_id: int):
    """Favori : passe devant les ex æquo dans les classements (départage admin)."""
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute(
            "SELECT is_favorite FROM participants WHERE id=?", (participant_id,)
        )
        p = await row.fetchone()
        if not p:
            raise HTTPException(404)
        new_val = 0 if p["is_favorite"] else 1
        await db.execute(
            "UPDATE participants SET is_favorite=? WHERE id=?", (new_val, participant_id)
        )
        await db.commit()
    return JSONResponse({"is_favorite": new_val})


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
                      m.score_team1, m.score_team2,
                      m.final_score_team1, m.final_score_team2,
                      m.result, m.qualifier_winner,
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
                      pt.total_goals, pt.submitted, pt.submitted_at,
                      COALESCE(pt.admin_entered, 0) as admin_entered
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
                      ba.admin_entered,
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

        bonus_questions = []
        if view == "bonus":
            bq_rows = await db.execute(
                """SELECT id, question_text, phase, answer_type, options, deadline
                   FROM bonus_questions ORDER BY deadline, id"""
            )
            bonus_questions = [dict(r) for r in await bq_rows.fetchall()]

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
        "bonus_questions": bonus_questions,
        "teams_48": TEAMS_48,
        "outsiders": OUTSIDERS,
        "scorer_options": get_scorer_options() if view == "pre_tournament" else [],
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
        if match_dict["phase"] != "group":
            if qualifier_prediction not in ("team1", "team2"):
                _flash(request, "Phase finale : indique l'équipe qualifiée.", "err")
                return redirect
            if prediction in ("team1", "team2") and qualifier_prediction != prediction:
                _flash(request, "Le qualifié est incohérent avec le score.", "err")
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


@router.post("/pronostics/force-pre-tournoi")
async def force_pre_tournament(request: Request,
                               participant_id: int = Form(...),
                               winner: str = Form(default=""),
                               finalist: str = Form(default=""),
                               top_scorer: str = Form(default=""),
                               revelation: str = Form(default=""),
                               total_goals: str = Form(default="")):
    """Encode des réponses pré-tournoi au nom d'un participant (ex: reçues
    par SMS avant la deadline). Seuls les champs remplis sont modifiés."""
    await require_admin(request)
    redirect = RedirectResponse("/admin/pronostics?view=pre_tournament", status_code=303)
    winner = winner.strip()
    finalist = finalist.strip()
    top_scorer = top_scorer.strip()
    revelation = revelation.strip()
    total_goals = total_goals.strip()
    incoming = {
        "winner": winner, "finalist": finalist, "top_scorer": top_scorer,
        "revelation": revelation, "total_goals": total_goals,
    }
    provided = {k: v for k, v in incoming.items() if v}
    if not provided:
        _flash(request, "Aucune réponse fournie : remplis au moins un champ.", "err")
        return redirect
    for team_value, label in ((winner, "champion"), (finalist, "autre finaliste")):
        if team_value and team_value not in TEAMS_48:
            _flash(request, f"Équipe inconnue pour {label} : {team_value}.", "err")
            return redirect
    if revelation and revelation not in OUTSIDERS:
        _flash(request, f"Outsider inconnu pour la révélation : {revelation}.", "err")
        return redirect
    if top_scorer and not is_valid_scorer(top_scorer):
        _flash(request, "Joueur inconnu pour le meilleur buteur.", "err")
        return redirect
    if total_goals:
        try:
            total_goals = int(total_goals)
        except ValueError:
            _flash(request, "Le total de buts doit être un nombre entier.", "err")
            return redirect
    async with get_db() as db:
        p_row = await db.execute(
            "SELECT * FROM participants WHERE id=? AND is_admin=0", (participant_id,)
        )
        participant = await p_row.fetchone()
        if not participant:
            _flash(request, "Participant introuvable.", "err")
            return redirect
        existing = await (await db.execute(
            "SELECT * FROM pre_tournament_predictions WHERE participant_id=?",
            (participant_id,),
        )).fetchone()
        values = {
            "winner": existing["winner"] if existing else None,
            "finalist": existing["finalist"] if existing else None,
            "top_scorer": existing["top_scorer"] if existing else None,
            "revelation": existing["revelation"] if existing else None,
            "total_goals": existing["total_goals"] if existing else None,
        }
        values.update(provided)
        if values["winner"] and values["finalist"] and values["winner"] == values["finalist"]:
            _flash(request, "Le champion et l'autre finaliste ne peuvent pas être identiques.", "err")
            return redirect
        submitted_at = _now_utc()
        if existing:
            await db.execute(
                """UPDATE pre_tournament_predictions
                   SET winner=?, finalist=?, top_scorer=?, revelation=?, total_goals=?,
                       submitted=1, submitted_at=?, admin_entered=1
                   WHERE participant_id=?""",
                (values["winner"], values["finalist"], values["top_scorer"],
                 values["revelation"], values["total_goals"], submitted_at, participant_id),
            )
        else:
            await db.execute(
                """INSERT INTO pre_tournament_predictions
                   (participant_id, winner, finalist, top_scorer, revelation,
                    total_goals, submitted, submitted_at, admin_entered)
                   VALUES (?,?,?,?,?,?,1,?,1)""",
                (participant_id, values["winner"], values["finalist"], values["top_scorer"],
                 values["revelation"], values["total_goals"], submitted_at),
            )
        await db.commit()
        name = participant["nickname"] or participant["name"]
        answered_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM pre_tournament_questions WHERE correct_answer IS NOT NULL"
        )
        has_answers = (await answered_row.fetchone())["cnt"] > 0
    if has_answers:
        await recalculate_pre_tournament_scores()
    msg = f"Pré-tournoi de {name} mis à jour ({', '.join(provided)})."
    if has_answers:
        msg += " Scores recalculés."
    _flash(request, msg)
    return redirect


@router.post("/pronostics/force-bonus")
async def force_bonus_answer(request: Request,
                             participant_id: int = Form(...),
                             question_id: int = Form(...),
                             answer: str = Form(default="")):
    """Encode une réponse bonus au nom d'un participant, même deadline passée."""
    await require_admin(request)
    redirect = RedirectResponse("/admin/pronostics?view=bonus", status_code=303)
    answer = answer.strip()
    if not answer:
        _flash(request, "Indique la réponse du participant.", "err")
        return redirect
    async with get_db() as db:
        p_row = await db.execute(
            "SELECT * FROM participants WHERE id=? AND is_admin=0", (participant_id,)
        )
        participant = await p_row.fetchone()
        q_row = await db.execute("SELECT * FROM bonus_questions WHERE id=?", (question_id,))
        question = await q_row.fetchone()
        if not participant or not question:
            _flash(request, "Participant ou question introuvable.", "err")
            return redirect
        if question["answer_type"] == "choice" and question["options"]:
            options = json.loads(question["options"])
            matched = next((o for o in options if o.lower() == answer.lower()), None)
            if not matched:
                _flash(request, f"Réponse invalide : choisis parmi {', '.join(options)}.", "err")
                return redirect
            answer = matched
        if question["answer_type"] == "number" and parse_bonus_number(answer) is None:
            _flash(request, "Réponse invalide : indique un nombre.", "err")
            return redirect
        old_row = await db.execute(
            "SELECT answer FROM bonus_answers WHERE participant_id=? AND question_id=?",
            (participant_id, question_id),
        )
        old = await old_row.fetchone()
        await db.execute(
            """INSERT INTO bonus_answers (participant_id, question_id, answer, admin_entered)
               VALUES (?,?,?,1)
               ON CONFLICT(participant_id, question_id) DO UPDATE SET
                 answer=excluded.answer,
                 admin_entered=1,
                 submitted_at=datetime('now')""",
            (participant_id, question_id, answer),
        )
        await db.commit()
        name = participant["nickname"] or participant["name"]
        has_answer = question["correct_answer"] is not None
    if has_answer:
        await calculate_bonus_scores(question_id)
    msg = f"Réponse « {answer} » enregistrée pour {name}."
    if old:
        msg += f" Remplace « {old['answer']} »."
    if has_answer:
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


# ---- Nouveautés (story) ----

def _slugify(text: str) -> str:
    out = []
    for ch in (text or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:40] or "news"


@router.get("/nouveautes", response_class=HTMLResponse)
async def news_admin(request: Request):
    await require_admin(request)
    from app.news import STORY_TEMPLATES
    async with get_db() as db:
        rows = await db.execute(
            "SELECT * FROM news_items ORDER BY sort_order, id"
        )
        items = [dict(r) for r in await rows.fetchall()]
    return templates.TemplateResponse(request, "admin/news.html", {
        "request": request,
        "active": "nouveautes",
        "flashes": _get_flashes(request),
        "items": items,
        "story_templates": STORY_TEMPLATES,
    })


def _clean_template_key(value: str) -> str | None:
    from app.news import is_valid_template_key
    value = (value or "").strip()
    return value if is_valid_template_key(value) else None


@router.post("/nouveautes")
async def news_create(
    request: Request,
    title: str = Form(...),
    body: str = Form(default=""),
    icon: str = Form(default=""),
    media_path: str = Form(default=""),
    template_key: str = Form(default=""),
    sort_order: int = Form(default=0),
    is_published: str = Form(default="0"),
):
    await require_admin(request)
    title = title.strip()
    if not title:
        _flash(request, "Le titre est obligatoire.", "err")
        return RedirectResponse("/admin/nouveautes", status_code=303)
    slug = f"{_slugify(title)}-{uuid.uuid4().hex[:4]}"
    async with get_db() as db:
        await db.execute(
            """INSERT INTO news_items (slug, title, body, icon, media_path, template_key, sort_order, is_published)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                slug, title[:120], body.strip(), icon.strip()[:8],
                media_path.strip()[:300], _clean_template_key(template_key), sort_order,
                1 if is_published == "1" else 0,
            ),
        )
        await db.commit()
    _flash(request, "Nouveauté créée.")
    return RedirectResponse("/admin/nouveautes", status_code=303)


@router.post("/nouveautes/{news_id}")
async def news_update(
    request: Request,
    news_id: int,
    title: str = Form(...),
    body: str = Form(default=""),
    icon: str = Form(default=""),
    media_path: str = Form(default=""),
    template_key: str = Form(default=""),
    sort_order: int = Form(default=0),
    is_published: str = Form(default="0"),
):
    await require_admin(request)
    title = title.strip()
    if not title:
        _flash(request, "Le titre est obligatoire.", "err")
        return RedirectResponse("/admin/nouveautes", status_code=303)
    async with get_db() as db:
        await db.execute(
            """UPDATE news_items
               SET title=?, body=?, icon=?, media_path=?, template_key=?, sort_order=?, is_published=?
               WHERE id=?""",
            (
                title[:120], body.strip(), icon.strip()[:8],
                media_path.strip()[:300], _clean_template_key(template_key), sort_order,
                1 if is_published == "1" else 0, news_id,
            ),
        )
        await db.commit()
    _flash(request, "Nouveauté mise à jour.")
    return RedirectResponse("/admin/nouveautes", status_code=303)


@router.get("/nouveautes/{news_id}/preview", response_class=HTMLResponse)
async def news_preview(request: Request, news_id: int):
    """Aperçu du rendu réel de la story (player + écrans), brouillons inclus."""
    await require_admin(request)
    from app.news import STORY_TEMPLATES
    async with get_db() as db:
        row = await db.execute(
            "SELECT id, slug, title, body, icon, media_path, template_key FROM news_items WHERE id=?",
            (news_id,),
        )
        item = await row.fetchone()
    if not item:
        raise HTTPException(404)
    item = dict(item)
    if item.get("template_key") not in STORY_TEMPLATES:
        item["template_key"] = None
    return templates.TemplateResponse(request, "admin/news_preview.html", {
        "request": request,
        "news_story": [item],
        "story_autoopen": "1",
    })


@router.post("/nouveautes/{news_id}/delete")
async def news_delete(request: Request, news_id: int):
    await require_admin(request)
    async with get_db() as db:
        await db.execute("DELETE FROM news_items WHERE id=?", (news_id,))
        await db.commit()
    _flash(request, "Nouveauté supprimée.")
    return RedirectResponse("/admin/nouveautes", status_code=303)


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
        # Repère lisible « Journée N » (groupe) / round court (phases finales) à côté
        # du n° officiel FIFA, qui n'est pas chronologique.
        if phase == "group":
            by_group = {}
            for m in matches:  # déjà triés par date/heure
                by_group.setdefault(m["group_name"], []).append(m)
            for group_matches in by_group.values():
                for idx, m in enumerate(group_matches):
                    m["round_label"] = f"Journée {min(idx // 2 + 1, 3)}"
        else:
            short = {"round_of_32": "16es", "round_of_16": "8es", "quarter": "Quarts",
                     "semi": "Demies", "third_place": "3e place", "final": "Finale"}
            for m in matches:
                m["round_label"] = short.get(phase, "")
            await enrich_knockout_matches(db, matches)
        counts = {}
        for ph in PHASE_LABELS:
            c_row = await db.execute("SELECT COUNT(*) as cnt FROM matches WHERE phase=?", (ph,))
            counts[ph] = (await c_row.fetchone())["cnt"]
    return templates.TemplateResponse(request, "admin/matches.html", {
        "request": request,
        "active": "matches",
        "flashes": _get_flashes(request),
        "matches": matches,
        "current_phase": phase,
        "phase_labels": PHASE_LABELS,
        "phase_counts": counts,
        "teams_48": TEAMS_48,
    })


@router.post("/matches/{match_id}/teams")
async def confirm_knockout_match_teams(
    request: Request,
    match_id: int,
    team1_name: str = Form(...),
    team2_name: str = Form(...),
    phase: str = Form(default="round_of_32"),
):
    await require_admin(request)
    async with get_db() as db:
        ok, msg = await confirm_match_teams(db, match_id, team1_name, team2_name)
        await db.commit()
    _flash(request, msg, "ok" if ok else "err")
    return RedirectResponse(f"/admin/matches?phase={phase}", status_code=303)


@router.post("/matches/{match_id}/teams/{side}")
async def confirm_knockout_match_side(
    request: Request,
    match_id: int,
    side: str,
    team1_name: str = Form(default=""),
    team2_name: str = Form(default=""),
    phase: str = Form(default="round_of_32"),
):
    await require_admin(request)
    team_name = team1_name if side == "team1" else team2_name
    async with get_db() as db:
        ok, msg = await confirm_match_side(db, match_id, side, team_name)
        await db.commit()
    _flash(request, msg, "ok" if ok else "err")
    return RedirectResponse(f"/admin/matches?phase={phase}", status_code=303)


@router.post("/matches/{match_id}/predictions-open/toggle")
async def toggle_match_predictions_open(request: Request, match_id: int):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        target = not bool(match["predictions_open"])
        ok, msg = await set_match_predictions_open(db, match_id, target)
        await db.commit()
        phase = match["phase"]
    _flash(request, msg, "ok" if ok else "err")
    return RedirectResponse(f"/admin/matches?phase={phase}", status_code=303)


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
                        final_score_team1: str = Form(default=""),
                        final_score_team2: str = Form(default=""),
                        qualifier_winner: str = Form(default=""),
                        redirect_to: str = Form(default="")):
    await require_admin(request)
    # L'encodage inline du tableau de bord renvoie vers le tableau de bord.
    back = "/admin/dashboard" if redirect_to == "dashboard" else "/admin/resultats"
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        match_dict = dict(match)
        payload, error = _match_result_payload(
            match_dict, score_team1, score_team2,
            final_score_team1, final_score_team2, qualifier_winner
        )
        if error:
            _flash(request, error, "err")
            return RedirectResponse(back, status_code=303)
        await db.execute(
            """UPDATE matches
               SET score_team1=?, score_team2=?, final_score_team1=?,
                   final_score_team2=?, result=?, qualifier_winner=?
               WHERE id=?""",
            (
                payload["score_team1"], payload["score_team2"],
                payload["final_score_team1"], payload["final_score_team2"],
                payload["result"], payload["qualifier_winner"], match_id,
            )
        )
        propagation = await propagate_from_match(db, match_id)
        await db.commit()
    await recalculate_match_scores(match_id)
    _flash(request, f"Résultat encodé. Scores recalculés.{_bracket_flash_suffix(propagation)}")
    return RedirectResponse(back, status_code=303)


@router.post("/resultats/{match_id}/correct")
async def correct_result(request: Request, match_id: int,
                         score_team1: int = Form(...), score_team2: int = Form(...),
                         final_score_team1: str = Form(default=""),
                         final_score_team2: str = Form(default=""),
                         qualifier_winner: str = Form(default="")):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
        match = await row.fetchone()
        if not match:
            raise HTTPException(404)
        match_dict = dict(match)
        payload, error = _match_result_payload(
            match_dict, score_team1, score_team2,
            final_score_team1, final_score_team2, qualifier_winner
        )
        if error:
            _flash(request, error, "err")
            return RedirectResponse("/admin/resultats", status_code=303)
        await db.execute(
            """UPDATE matches
               SET score_team1=?, score_team2=?, final_score_team1=?,
                   final_score_team2=?, result=?, qualifier_winner=?
               WHERE id=?""",
            (
                payload["score_team1"], payload["score_team2"],
                payload["final_score_team1"], payload["final_score_team2"],
                payload["result"], payload["qualifier_winner"], match_id,
            )
        )
        propagation = await propagate_from_match(db, match_id)
        await db.commit()
    await recalculate_match_scores(match_id)
    _flash(request, f"Correction appliquée. Scores recalculés.{_bracket_flash_suffix(propagation)}")
    return RedirectResponse("/admin/resultats", status_code=303)


# ---- Bonus Questions ----

@router.get("/bonus", response_class=HTMLResponse)
async def bonus_admin(request: Request):
    await require_admin(request)
    async with get_db() as db:
        now = _now_utc()
        pt_deadline = await get_pre_tournament_deadline(db)
        confirmed_row = await db.execute(
            "SELECT COUNT(*) AS cnt FROM participants WHERE is_confirmed=1 AND is_admin=0"
        )
        confirmed_count = (await confirmed_row.fetchone())["cnt"]
        rows = await db.execute(
            """SELECT bq.*,
                      COUNT(DISTINCT CASE WHEN p.is_admin=0 THEN ba.id END) as answer_count,
                      (SELECT COUNT(*) FROM scores s
                       JOIN participants sp ON sp.id=s.participant_id
                       WHERE s.bonus_question_id = bq.id
                         AND s.points > 0
                         AND sp.is_admin=0) as scored_answer_count
               FROM bonus_questions bq
               LEFT JOIN bonus_answers ba ON ba.question_id = bq.id
               LEFT JOIN participants p ON p.id = ba.participant_id
               GROUP BY bq.id
               ORDER BY bq.deadline"""
        )
        questions = [dict(r) for r in await rows.fetchall()]
        bonus_status_counts = {"draft": 0, "open": 0, "to_score": 0, "scored": 0}
        for question in questions:
            try:
                opts = json.loads(question["options"]) if question.get("options") else []
            except Exception:
                opts = []
            question["options_text"] = "\n".join(opts)
            question["scored_answer_count"] = question.get("scored_answer_count") or 0
            closest_config = normalize_closest_config(
                question["points_value"], question.get("scoring_config")
            )
            question["closest_preset_key"] = closest_config["preset_key"]
            question["closest_award_mode"] = closest_config["award_mode"]
            question["closest_tie_policy"] = closest_config["tie_policy"]
            question["closest_rank_points"] = closest_config["rank_points"]
            question["preview_can_edit"] = question["deadline"] > now
            question["participant_total"] = confirmed_count
            if not question["is_published"]:
                question["status_key"] = "draft"
                question["status_label"] = "Brouillon"
                question["status_class"] = "warn"
                question["default_open"] = True
            elif question["deadline"] > now:
                question["status_key"] = "open"
                question["status_label"] = "Ouverte"
                question["status_class"] = "ok"
                question["default_open"] = False
            elif question.get("correct_answer"):
                question["status_key"] = "scored"
                question["status_label"] = "Scorée"
                question["status_class"] = "ok"
                question["default_open"] = False
            else:
                question["status_key"] = "to_score"
                question["status_label"] = "À corriger"
                question["status_class"] = "lock"
                question["default_open"] = True
            bonus_status_counts[question["status_key"]] += 1
            if question["answer_type"] == "multi_choice":
                question["correct_set"] = sorted(parse_team_set(question.get("correct_answer")))
                question["correct_count"] = None
                question["correct_answer_display"] = format_team_list(question.get("correct_answer"))
                question["locked_teams"] = set()
                question["points_label"] = f"{question['points_value']} pts"
            elif question["answer_type"] == "number_multi":
                parsed = parse_number_multi(question.get("correct_answer"))
                number_config = normalize_number_multi_config(
                    question["points_value"],
                    question.get("scoring_config"),
                    opts,
                )
                question["correct_set"] = sorted(parsed["teams"])
                question["correct_count"] = parsed["count"]
                question["correct_answer_display"] = format_number_multi(
                    question.get("correct_answer"),
                    opts,
                )
                question["number_multi_config"] = number_config
                question["locked_teams"] = set(number_config["locked_teams"])
                question["points_label"] = f"{number_config['max_points']} pts max"
            else:
                question["correct_set"] = []
                question["correct_count"] = None
                question["correct_answer_display"] = question.get("correct_answer") or ""
                question["locked_teams"] = set()
                question["points_label"] = (
                    f"{' / '.join(str(p) for p in question['closest_rank_points'])} pts"
                    if question["scoring_mode"] == "closest_podium"
                    else f"{question['points_value']} pts"
                )
            question["search_text"] = " ".join([
                question.get("question_text") or "",
                question.get("status_label") or "",
                PHASE_LABELS.get(question.get("phase"), question.get("phase") or ""),
                question.get("points_label") or "",
            ])
        questions.sort(key=lambda item: (
            {"to_score": 0, "draft": 1, "open": 2, "scored": 3}.get(item["status_key"], 9),
            item["deadline"],
            item["id"],
        ))
        team_ids = {q["id"] for q in questions if q["answer_type"] == "multi_choice"}
        combo_ids = {q["id"] for q in questions if q["answer_type"] == "number_multi"}
        answer_rows = await db.execute(
            """SELECT
                  ba.question_id,
                  ba.participant_id,
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
            if answer_dict["question_id"] in team_ids:
                answer_dict["answer_display"] = format_team_list(answer_dict.get("answer"))
            elif answer_dict["question_id"] in combo_ids:
                options = next((q.get("options_text", "").split("\n") for q in questions if q["id"] == answer_dict["question_id"]), [])
                answer_dict["answer_display"] = format_number_multi(answer_dict.get("answer"), options)
            else:
                answer_dict["answer_display"] = answer_dict.get("answer")
            answers_by_question.setdefault(answer_dict["question_id"], []).append(answer_dict)
        closest_standings_by_question = {}
        for question in questions:
            if question["scoring_mode"] != "closest_podium":
                continue
            if not question.get("correct_answer"):
                continue
            closest_standings_by_question[question["id"]] = closest_bonus_standings(
                question["points_value"],
                question["correct_answer"],
                answers_by_question.get(question["id"], []),
                question.get("scoring_config"),
            )
        pt_sub_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM pre_tournament_predictions WHERE submitted=1"
        )
        pt_submitted_count = (await pt_sub_row.fetchone())["cnt"]
        pt_total_row = await db.execute(
            "SELECT COUNT(*) as cnt FROM participants WHERE is_confirmed=1 AND is_admin=0"
        )
        pt_total_count = (await pt_total_row.fetchone())["cnt"]
    return templates.TemplateResponse(request, "admin/bonus.html", {
        "request": request,
        "active": "bonus",
        "flashes": _get_flashes(request),
        "questions": questions,
        "bonus_status_counts": bonus_status_counts,
        "phase_labels": {"pre_tournament": "Pré-tournoi", **PHASE_LABELS},
        "pt_submitted_count": pt_submitted_count,
        "pt_total_count": pt_total_count,
        "pt_deadline": pt_deadline,
        "answers_by_question": answers_by_question,
        "closest_standings_by_question": closest_standings_by_question,
    })


@router.post("/bonus/create")
async def create_bonus(request: Request,
                       question_text: str = Form(...), question_title: str = Form(default=""),
                       phase: str = Form(...),
                       answer_type: str = Form(...), points_value: int = Form(...),
                       deadline: str = Form(...), timezone_name: str = Form(default=""),
                       options_text: str = Form(default=""),
                       correct_answer: str = Form(default=""),
                       help_text: str = Form(default=""),
                       is_published: int = Form(default=0),
                       closest_preset_key: str = Form(default="fun_balanced"),
                       closest_award_mode: str = Form(default="podium_custom"),
                       closest_tie_policy: str = Form(default="full_skip"),
                       closest_rank1_points: int = Form(default=6),
                       closest_rank2_points: int = Form(default=4),
                       closest_rank3_points: int = Form(default=2)):
    await require_admin(request)
    question_text = _compose_question_text(question_title, question_text)
    if answer_type not in {"choice", "number", "multi_choice", "number_multi"}:
        _flash(request, "Type de question bonus invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    if phase not in BONUS_PHASES:
        _flash(request, "Phase de question bonus invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    try:
        deadline_utc = local_input_to_utc_iso(deadline, timezone_name)
    except Exception:
        _flash(request, "Deadline invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    if answer_type == "multi_choice":
        scoring_mode = "multi_select"
    elif answer_type == "number_multi":
        scoring_mode = "number_multi"
    elif answer_type == "number":
        scoring_mode = "closest_podium"
    else:
        scoring_mode = "exact"
    points_value, scoring_config = _closest_form_config(
        answer_type,
        points_value,
        closest_preset_key,
        closest_award_mode,
        closest_tie_policy,
        closest_rank1_points,
        closest_rank2_points,
        closest_rank3_points,
    )
    options = _normalize_bonus_options(answer_type, options_text)
    if answer_type == "multi_choice":
        scoring_config = json.dumps({"error_step": 2}, ensure_ascii=False)
        # La réponse correcte multi-choix se renseigne via l'édition (cases à cocher).
        correct_answer = ""
    elif answer_type == "number_multi":
        scoring_config = _number_multi_scoring_config(points_value, {
            "part1_points": 3,
            "team_step": 1,
        }, json.loads(options) if options else [])
        # La réponse correcte (total + équipes) se renseigne via l'édition.
        correct_answer = ""
    if answer_type in ("choice", "multi_choice", "number_multi") and (not options or len(json.loads(options)) < 2):
        _flash(request, "Ajoute au moins deux options de réponse.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    if scoring_mode == "closest_podium" and correct_answer.strip():
        if parse_bonus_number(correct_answer) is None:
            _flash(request, "La réponse correcte doit être un nombre.", "err")
            return RedirectResponse("/admin/bonus", status_code=303)
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO bonus_questions
               (question_text, phase, answer_type, options, points_value,
                correct_answer, scoring_mode, scoring_config, help_text,
                is_published, deadline)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                question_text.strip(),
                phase,
                answer_type,
                options,
                points_value,
                correct_answer.strip() or None,
                scoring_mode,
                scoring_config,
                help_text.strip() or None,
                1 if is_published else 0,
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
    question_title: str = Form(default=""),
    phase: str = Form(...),
    answer_type: str = Form(default=""),
    points_value: int = Form(...),
    deadline: str = Form(...),
    timezone_name: str = Form(default=""),
    options_text: str = Form(default=""),
    correct_answer: str = Form(default=""),
    help_text: str = Form(default=""),
    is_published: int = Form(default=0),
    closest_preset_key: str = Form(default="custom"),
    closest_award_mode: str = Form(default="podium_custom"),
    closest_tie_policy: str = Form(default="full_skip"),
    closest_rank1_points: int = Form(default=6),
    closest_rank2_points: int = Form(default=4),
    closest_rank3_points: int = Form(default=2),
):
    await require_admin(request)
    question_text = _compose_question_text(question_title, question_text)
    if phase not in BONUS_PHASES:
        _flash(request, "Phase de question bonus invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    try:
        deadline_utc = local_input_to_utc_iso(deadline, timezone_name)
    except Exception:
        _flash(request, "Deadline invalide.", "err")
        return RedirectResponse("/admin/bonus", status_code=303)
    async with get_db() as db:
        row = await db.execute(
            """SELECT bq.*,
                      (SELECT COUNT(*) FROM bonus_answers ba WHERE ba.question_id=bq.id) AS answer_count
               FROM bonus_questions bq
               WHERE bq.id=?""",
            (question_id,),
        )
        existing = await row.fetchone()
        if not existing:
            raise HTTPException(404)
        type_can_change = not existing["is_published"] and existing["answer_count"] == 0
        answer_type = answer_type if type_can_change and answer_type else existing["answer_type"]
        if answer_type not in {"choice", "number", "multi_choice", "number_multi"}:
            _flash(request, "Type de question bonus invalide.", "err")
            return RedirectResponse("/admin/bonus", status_code=303)
        if answer_type == "multi_choice":
            scoring_mode = "multi_select"
        elif answer_type == "number_multi":
            scoring_mode = "number_multi"
        elif answer_type == "number":
            scoring_mode = "closest_podium"
        else:
            scoring_mode = "exact"
        points_value, scoring_config = _closest_form_config(
            answer_type,
            points_value,
            closest_preset_key,
            closest_award_mode,
            closest_tie_policy,
            closest_rank1_points,
            closest_rank2_points,
            closest_rank3_points,
        )
        if answer_type == "number":
            scoring_config = _preserve_closest_value_bounds(
                scoring_config,
                existing["scoring_config"],
            )
        options = _normalize_bonus_options(answer_type, options_text)
        if answer_type in ("choice", "multi_choice", "number_multi") and (not options or len(json.loads(options)) < 2):
            _flash(request, "Ajoute au moins deux options de réponse.", "err")
            return RedirectResponse("/admin/bonus", status_code=303)
        if answer_type == "multi_choice":
            scoring_config = json.dumps({"error_step": 2}, ensure_ascii=False)
            # Réponse correcte = cases cochées (plusieurs valeurs "correct_answer").
            form = await request.form()
            valid_options = set(json.loads(options)) if options else set()
            selected = [v for v in form.getlist("correct_answer") if v in valid_options]
            correct_answer = json.dumps(selected, ensure_ascii=False) if selected else ""
        elif answer_type == "number_multi":
            option_list = json.loads(options) if options else []
            scoring_config = _number_multi_scoring_config(
                points_value,
                existing["scoring_config"],
                option_list,
            )
            # Réponse correcte = total (correct_count) + équipes cochées (correct_answer).
            form = await request.form()
            correct_answer, error = _number_multi_correct_from_form(
                form,
                option_list,
                scoring_config,
            )
            if error:
                _flash(request, error, "err")
                return RedirectResponse("/admin/bonus", status_code=303)
        if scoring_mode == "closest_podium" and correct_answer.strip():
            if parse_bonus_number(correct_answer) is None:
                _flash(request, "La réponse correcte doit être un nombre.", "err")
                return RedirectResponse("/admin/bonus", status_code=303)
        await db.execute(
            """UPDATE bonus_questions
               SET question_text=?, phase=?, answer_type=?, options=?,
                   points_value=?, correct_answer=?, scoring_mode=?,
                   scoring_config=?, help_text=?, is_published=?, deadline=?
               WHERE id=?""",
            (
                question_text.strip(),
                phase,
                answer_type,
                options,
                points_value,
                correct_answer.strip() or None,
                scoring_mode,
                scoring_config,
                help_text.strip() or None,
                1 if is_published else 0,
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
                            correct_answer: str = Form(default="")):
    await require_admin(request)
    async with get_db() as db:
        row = await db.execute(
            "SELECT scoring_mode, options, scoring_config FROM bonus_questions WHERE id=?", (question_id,)
        )
        question = await row.fetchone()
        if not question:
            raise HTTPException(404)
        if question["scoring_mode"] == "multi_select":
            form = await request.form()
            valid_options = set(json.loads(question["options"])) if question["options"] else set()
            selected = [v for v in form.getlist("correct_answer") if v in valid_options]
            correct_answer = json.dumps(selected, ensure_ascii=False) if selected else ""
        if question["scoring_mode"] == "number_multi":
            form = await request.form()
            option_list = json.loads(question["options"]) if question["options"] else []
            correct_answer, error = _number_multi_correct_from_form(
                form,
                option_list,
                question["scoring_config"],
            )
            if error:
                _flash(request, error, "err")
                return RedirectResponse("/admin/bonus", status_code=303)
        if question["scoring_mode"] == "closest_podium" and parse_bonus_number(correct_answer) is None:
            _flash(request, "La réponse correcte doit être un nombre.", "err")
            return RedirectResponse("/admin/bonus", status_code=303)
        await db.execute(
            "UPDATE bonus_questions SET correct_answer=? WHERE id=?",
            (correct_answer.strip() or None, question_id)
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
        context = await _communications_context(request, db)
    return templates.TemplateResponse(request, "admin/communications.html", context)


@router.post("/communications/deadline-reminders/preview", response_class=HTMLResponse)
async def preview_deadline_reminders(
    request: Request,
    deadline_keys: list[str] = Form(default=[]),
):
    await require_admin(request)
    selected_keys = list(dict.fromkeys(_normalize_deadline_iso(key) for key in deadline_keys if key))
    if not selected_keys:
        _flash(request, "Sélectionne au moins une deadline ouverte à relancer.", "err")
        return RedirectResponse("/admin/communications", status_code=303)
    async with get_db() as db:
        preview = await _build_deadline_preview(db, selected_keys)
        if not preview["groups"]:
            _flash(request, "Aucune deadline sélectionnée n'est encore ouverte avec des retardataires.", "err")
            return RedirectResponse("/admin/communications", status_code=303)
        context = await _communications_context(request, db, deadline_preview=preview)
    return templates.TemplateResponse(request, "admin/communications.html", context)


@router.post("/communications/deadline-reminders/send")
async def send_deadline_reminders(
    request: Request,
    deadline_keys: list[str] = Form(default=[]),
):
    await require_admin(request)
    selected_keys = list(dict.fromkeys(_normalize_deadline_iso(key) for key in deadline_keys if key))
    if not selected_keys:
        _flash(request, "Sélectionne au moins une deadline ouverte à relancer.", "err")
        return RedirectResponse("/admin/communications", status_code=303)
    if not push_enabled():
        _flash(request, "Notifications push non configurées: clés VAPID manquantes.", "err")
        return RedirectResponse("/admin/communications", status_code=303)

    async with get_db() as db:
        preview = await _build_deadline_preview(db, selected_keys)
        participants = preview["participants"]
        if not participants:
            _flash(request, "Aucun participant à relancer pour ces deadlines.", "err")
            return RedirectResponse("/admin/communications", status_code=303)

        sent_count = 0
        attempted_count = 0
        duplicate_count = preview["already_reminded_count"]
        today = parse_utc_iso(_now_utc()).astimezone(DISPLAY_TZ).strftime("%Y-%m-%d")
        for participant in participants:
            if participant["subscription_count"] <= 0:
                continue
            attempted_count += 1
            delivered = await send_push_to_participant(
                db,
                participant["id"],
                title=_deadline_push_title(participant["missing_count"]),
                body=_deadline_push_body(participant["missing"]),
                url=f"{settings.BASE_URL.rstrip('/')}/p/{participant['token']}",
            )
            if delivered:
                sent_count += 1
            for deadline in participant["deadline_keys"]:
                await db.execute(
                    """INSERT INTO notification_log (participant_id, kind, ref)
                       VALUES (?,?,?)
                       ON CONFLICT(participant_id, kind, ref) DO NOTHING""",
                    (
                        participant["id"],
                        DEADLINE_REMINDER_KIND,
                        _admin_deadline_reminder_ref(deadline, today),
                    ),
                )
        await db.commit()

    not_reachable = preview["unreachable_count"]
    failed_count = attempted_count - sent_count
    msg = (
        f"{sent_count}/{attempted_count} rappel(s) groupé(s) envoyés. "
        f"{not_reachable} participant(s) sans push actif."
    )
    if failed_count:
        msg += f" {failed_count} échec(s) push."
    if duplicate_count:
        msg += f" {duplicate_count} participant(s) avaient déjà reçu un rappel aujourd'hui."
    _flash(request, msg, "ok" if sent_count else "err")
    return RedirectResponse("/admin/communications", status_code=303)


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
