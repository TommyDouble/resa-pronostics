import json
from html import unescape
from pathlib import Path
import uuid

import app.routers.pages as page_routes

from app.database import (
    ROUND32_EXACT_POINTS,
    ROUND32_FAVORITE_CONCEDE_TEXT,
    ROUND32_TIEBREAK_COUNT_TEXT,
    ensure_round_of_32_bonus_drafts,
    get_db,
)
from conftest import run


def _fetch_question_by_text(text_like):
    async def _q():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM bonus_questions WHERE question_text LIKE ?",
                (f"%{text_like}%",),
            )
            r = await row.fetchone()
            return dict(r) if r else None
    return run(_q())


def test_round_of_32_drafts_seeded(client):
    # The migration seeds the three "seizièmes" questions as drafts.
    afrique = _fetch_question_by_text("Afrique Mode Patron")
    assert afrique is not None
    assert afrique["answer_type"] == "number_multi"
    assert afrique["scoring_mode"] == "number_multi"
    assert afrique["is_published"] == 0
    options = json.loads(afrique["options"])
    assert options[0] == "Maroc"
    assert "Sénégal" in options and len(options) == 8
    config = json.loads(afrique["scoring_config"])
    assert config["locked_teams"] == ["Maroc"]
    assert config["max_points"] == 10
    assert afrique["points_value"] == 10
    # The two obsolete v1 Afrique questions must be gone.
    assert _fetch_question_by_text("Afrique Mode Patron (expert)") is None

    favori = _fetch_question_by_text("Le Favori Qui Tremble")
    assert favori is not None
    assert favori["answer_type"] == "number"
    assert favori["scoring_mode"] == "exact"
    assert favori["points_value"] == ROUND32_EXACT_POINTS
    favori_config = json.loads(favori["scoring_config"])
    assert favori_config["min_value"] == 0
    assert favori_config["max_value"] == 6
    assert favori["help_text"] and "but contre son camp" in favori["help_text"]
    assert "5 pts" in favori["help_text"]

    assert _fetch_question_by_text("au moins une nouvelle séance") is None

    tab_count = _fetch_question_by_text("Combien de nouvelles séances")
    assert tab_count is not None
    assert tab_count["answer_type"] == "number"
    assert tab_count["scoring_mode"] == "exact"
    assert tab_count["points_value"] == ROUND32_EXACT_POINTS
    tab_config = json.loads(tab_count["scoring_config"])
    assert tab_config["min_value"] == 0
    assert tab_config["max_value"] == 13
    assert "5 pts" in tab_count["help_text"]


def test_multi_choice_end_to_end(client, admin_client, participant):
    # Create a published multi_choice question via the admin form.
    resp = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": "Test multi quelles équipes",
            "phase": "round_of_32",
            "answer_type": "multi_choice",
            "points_value": "6",
            "deadline": "2030-01-01T12:00",
            "options_text": "Alpha\nBeta\nGamma",
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    q = _fetch_question_by_text("Test multi quelles équipes")
    assert q is not None and q["scoring_mode"] == "multi_select"
    qid = q["id"]

    # Participant checks two boxes (multiple "answer" values).
    resp = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"answer": ["Alpha", "Beta"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    stored = run(_stored_answer(participant["id"], qid))
    assert set(json.loads(stored)) == {"Alpha", "Beta"}

    # Admin sets the correct answer (Alpha + Gamma) via update → 2 errors → 2 pts.
    resp = admin_client.post(
        f"/admin/bonus/{qid}/update",
        data={
            "question_text": "Test multi quelles équipes",
            "phase": "round_of_32",
            "answer_type": "multi_choice",
            "points_value": "6",
            "deadline": "2030-01-01T12:00",
            "options_text": "Alpha\nBeta\nGamma",
            "correct_answer": ["Alpha", "Gamma"],
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    points = run(_score(participant["id"], qid))
    assert points == 2


def test_number_multi_end_to_end(client, admin_client, participant):
    resp = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": "Test combo nombre et équipes",
            "phase": "round_of_32",
            "answer_type": "number_multi",
            "points_value": "3",
            "deadline": "2030-01-01T12:00",
            "options_text": "Alpha\nBeta\nGamma\nDelta",
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    q = _fetch_question_by_text("Test combo nombre et équipes")
    assert q is not None and q["scoring_mode"] == "number_multi"
    qid = q["id"]

    # Participant: total = 3 (so must pick exactly 3 teams for a generic number_multi).
    resp = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"count": "3", "teams": ["Alpha", "Beta", "Delta"]},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    stored = json.loads(run(_stored_answer(participant["id"], qid)))
    assert stored["count"] == 3 and set(stored["teams"]) == {"Alpha", "Beta", "Delta"}

    # Wrong team count is rejected (must equal total).
    bad = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"count": "3", "teams": ["Alpha", "Beta"]},
        follow_redirects=False,
    )
    assert bad.status_code == 400

    # Admin correct answer: total 3, teams Alpha + Gamma + Delta.
    # Participant: count exact (3) → +3 ; Alpha+Delta right (+2), Beta wrong (-1) → +1 ; total 4.
    resp = admin_client.post(
        f"/admin/bonus/{qid}/update",
        data={
            "question_text": "Test combo nombre et équipes",
            "phase": "round_of_32",
            "answer_type": "number_multi",
            "points_value": "3",
            "deadline": "2030-01-01T12:00",
            "options_text": "Alpha\nBeta\nGamma\nDelta",
            "correct_count": "3",
            "correct_answer": ["Alpha", "Gamma", "Delta"],
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert run(_score(participant["id"], qid)) == 4


def test_africa_number_multi_locks_maroc_and_validates_total(client, admin_client, participant):
    q = _fetch_question_by_text("Afrique Mode Patron")
    options = json.loads(q["options"])
    qid = q["id"]
    assert "Maroc" in options

    resp = admin_client.post(
        f"/admin/bonus/{qid}/update",
        data={
            "question_text": q["question_text"],
            "phase": "round_of_32",
            "answer_type": "number_multi",
            "points_value": "10",
            "deadline": "2030-01-01T12:00",
            "options_text": "\n".join(options),
            "is_published": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Client can omit Maroc; the server stores it anyway because it is locked.
    resp = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"count": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    stored = json.loads(run(_stored_answer(participant["id"], qid)))
    assert stored == {"count": 1, "teams": ["Maroc"]}

    bad = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"count": "4", "teams": ["Sénégal", "Égypte"]},
        follow_redirects=False,
    )
    assert bad.status_code == 400

    ok = client.post(
        f"/p/{participant['token']}/bonus/{qid}",
        data={"count": "4", "teams": ["Sénégal", "Égypte", "Ghana"]},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    stored = json.loads(run(_stored_answer(participant["id"], qid)))
    assert stored["count"] == 4
    assert set(stored["teams"]) == {"Maroc", "Sénégal", "Égypte", "Ghana"}


def test_number_multi_admin_rejects_invalid_correct_answer(admin_client):
    q = _fetch_question_by_text("Afrique Mode Patron")
    options = json.loads(q["options"])
    base = {
        "question_text": q["question_text"],
        "phase": "round_of_32",
        "answer_type": "number_multi",
        "points_value": "10",
        "deadline": "2030-01-01T12:00",
        "options_text": "\n".join(options),
        "is_published": "1",
    }

    invalid_cases = [
        {"correct_answer": ["Sénégal"]},
        {"correct_count": "abc", "correct_answer": ["Sénégal"]},
        {"correct_count": "4", "correct_answer": ["Sénégal"]},
    ]
    for extra in invalid_cases:
        data = {**base, **extra}
        resp = admin_client.post(
            f"/admin/bonus/{q['id']}/update",
            data=data,
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert _fetch_question_by_text("Afrique Mode Patron")["correct_answer"] is None


def test_home_bonus_stack_and_ajax_submit(client, admin_client, participant, monkeypatch):
    async def closed_pre_tournament(db, participant_id):
        return {
            "open": False,
            "complete": True,
            "filled_count": 0,
            "question_count": 5,
            "deadline": None,
        }

    monkeypatch.setattr(page_routes, "_pt_status", closed_pre_tournament)
    q = _fetch_question_by_text("Afrique Mode Patron")
    async def _publish_round32():
        async with get_db() as db:
            await db.execute(
                "UPDATE bonus_questions SET is_published=1, deadline=? WHERE phase=?",
                ("2030-01-01T12:00:00", "round_of_32"),
            )
            await db.commit()

    run(_publish_round32())
    html = unescape(client.get(f"/p/{participant['token']}").text)
    assert "data-bonus-stack" in html
    assert "data-bonus-stack-open" in html
    assert "Afrique Mode Patron" in html
    assert "Encore des tirs au but ?" in html
    assert "au moins une nouvelle séance" not in html
    # Bonus = action prioritaire ici → un seul CTA bonus, pas de doublon secondaire.
    assert "home-cta-bonus" not in html

    resp = client.post(
        f"/p/{participant['token']}/bonus/{q['id']}",
        data={"count": "1"},
        headers={"X-RESA-Bonus-Stack": "1"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "question_id": q["id"]}

    html_after = unescape(client.get(f"/p/{participant['token']}").text)
    assert "questions bonus attend" in html_after
    assert f'data-question-id="{q["id"]}"' not in html_after


def test_bonus_stack_mobile_input_and_tooltip_contract():
    css = Path("app/static/css/resa.css").read_text()
    js = Path("app/static/js/resa.js").read_text()

    tooltip_css = css.split(".floating-tooltip {", 1)[1].split("}", 1)[0]
    assert "z-index: 1200;" in tooltip_css

    stack_css = css.split(".bonus-stack-card .bonus-answer-form", 1)[1].split(
        ".bonus-stack-done", 1
    )[0]
    assert '.bonus-stack-card input[type="number"]' in stack_css
    assert "font-size: 16px;" in stack_css

    tooltip_js = js.split("function initFloatingTooltips()", 1)[1].split(
        "/* ---- Prediction anchor", 1
    )[0]
    assert "aria-expanded" in tooltip_js
    assert "toggleTooltip(trigger)" in tooltip_js
    assert "touchstart" in tooltip_js

    focus_js = js.split("function focusActive()", 1)[1].split(
        "function updateDeck()", 1
    )[0]
    assert "active.focus({ preventScroll: true });" in focus_js
    assert "querySelector('input" not in focus_js
    assert "querySelector('button" not in focus_js


def test_bonus_title_and_prompt_are_joined(client, admin_client):
    # Two separate admin fields are stored as "Title — Prompt".
    resp = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": "Combien de buts au total ?",
            "question_title": "Festival offensif",
            "phase": "round_of_32",
            "answer_type": "number",
            "points_value": "6",
            "deadline": "2030-01-01T12:00",
            "is_published": "0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    q = _fetch_question_by_text("Festival offensif")
    assert q is not None
    assert q["question_text"] == "Festival offensif — Combien de buts au total ?"

    # Empty title → only the prompt is stored (no leading separator).
    resp = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": "Question sans titre court",
            "question_title": "",
            "phase": "round_of_32",
            "answer_type": "number",
            "points_value": "6",
            "deadline": "2030-01-01T12:00",
            "is_published": "0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    q2 = _fetch_question_by_text("Question sans titre court")
    assert q2 is not None
    assert q2["question_text"] == "Question sans titre court"


def test_home_shows_secondary_bonus_cta_when_pretournament_is_priority(
    client, participant, monkeypatch
):
    # Pré-tournoi ouvert & incomplet => action prioritaire = pré-tournoi.
    async def open_pre_tournament(db, participant_id):
        return {
            "open": True,
            "complete": False,
            "filled_count": 0,
            "question_count": 5,
            "deadline": "2030-01-01T12:00:00",
        }

    monkeypatch.setattr(page_routes, "_pt_status", open_pre_tournament)

    async def _publish_round32():
        async with get_db() as db:
            await db.execute(
                "UPDATE bonus_questions SET is_published=1, deadline=? WHERE phase=?",
                ("2030-01-01T12:00:00", "round_of_32"),
            )
            await db.commit()

    run(_publish_round32())
    html = unescape(client.get(f"/p/{participant['token']}").text)
    # L'action prioritaire (pré-tournoi) ET le CTA bonus secondaire coexistent.
    assert "/pre-tournoi" in html
    assert "home-cta-bonus" in html
    assert "questions bonus attend" in html or "question bonus attend" in html


def _publish_question(qid, deadline):
    async def _go():
        async with get_db() as db:
            await db.execute(
                "UPDATE bonus_questions SET is_published=1, deadline=? WHERE id=?",
                (deadline, qid),
            )
            await db.commit()
    run(_go())


def _create_participant(name):
    token = str(uuid.uuid4())

    async def _go():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants
                   (name, first_name, last_name, email, token, is_confirmed)
                   VALUES (?,?,?,?,?,1)""",
                (name, name.split()[0], "Exact", f"{token}@test.local", token),
            )
            await db.commit()
            return {"id": cursor.lastrowid, "token": token}

    return run(_go())


def _delete_participants(participant_ids):
    if not participant_ids:
        return

    async def _go():
        async with get_db() as db:
            placeholders = ",".join("?" for _ in participant_ids)
            await db.execute(
                f"DELETE FROM scores WHERE participant_id IN ({placeholders})",
                tuple(participant_ids),
            )
            await db.execute(
                f"DELETE FROM bonus_answers WHERE participant_id IN ({placeholders})",
                tuple(participant_ids),
            )
            await db.execute(
                f"DELETE FROM participants WHERE id IN ({placeholders})",
                tuple(participant_ids),
            )
            await db.commit()

    run(_go())


def test_bonus_number_rejects_out_of_bounds(client, participant):
    # "Le Favori Qui Tremble" is seeded with min_value=0 / max_value=6.
    q = _fetch_question_by_text("Le Favori Qui Tremble")
    assert q is not None and q["answer_type"] == "number"
    _publish_question(q["id"], "2030-01-01T12:00:00")
    base = f"/p/{participant['token']}/bonus/{q['id']}"

    too_high = client.post(base, data={"answer": "7"}, follow_redirects=False)
    assert too_high.status_code == 400

    negative = client.post(base, data={"answer": "-1"}, follow_redirects=False)
    assert negative.status_code == 400

    ok = client.post(base, data={"answer": "3"}, follow_redirects=False)
    assert ok.status_code == 303
    stored = run(_stored_answer(participant["id"], q["id"]))
    assert stored == "3"


def test_round32_numeric_bonus_only_exact_answers_score(client, admin_client):
    scenarios = [
        ("Le Favori Qui Tremble", "3", "2"),
        ("Encore des tirs au but ?", "1", "2"),
    ]
    created_ids = []
    try:
        for label, correct_answer, wrong_answer in scenarios:
            q = _fetch_question_by_text(label)
            exact = _create_participant(f"{label} Exact")
            wrong = _create_participant(f"{label} Wrong")
            created_ids.extend([exact["id"], wrong["id"]])
            _publish_question(q["id"], "2030-01-01T12:00:00")

            assert client.post(
                f"/p/{exact['token']}/bonus/{q['id']}",
                data={"answer": correct_answer},
                follow_redirects=False,
            ).status_code == 303
            assert client.post(
                f"/p/{wrong['token']}/bonus/{q['id']}",
                data={"answer": wrong_answer},
                follow_redirects=False,
            ).status_code == 303

            resp = admin_client.post(
                f"/admin/bonus/{q['id']}/answer",
                data={"correct_answer": correct_answer},
                follow_redirects=False,
            )
            assert resp.status_code == 303

            assert run(_score(exact["id"], q["id"])) == ROUND32_EXACT_POINTS
            assert run(_score(wrong["id"], q["id"])) == 0
    finally:
        _delete_participants(created_ids)


def test_round32_exact_numeric_migration_updates_existing_questions_and_scores():
    right = _create_participant("Round32 Migration Exact")
    wrong = _create_participant("Round32 Migration Wrong")

    async def _downgrade_then_migrate():
        async with get_db() as db:
            await db.execute(
                "DELETE FROM app_settings WHERE key='migr_round32_exact_numeric_bonus_v1'"
            )
            for text in (ROUND32_TIEBREAK_COUNT_TEXT, ROUND32_FAVORITE_CONCEDE_TEXT):
                row = await db.execute(
                    "SELECT id FROM bonus_questions WHERE question_text=?",
                    (text,),
                )
                question = await row.fetchone()
                qid = question["id"]
                await db.execute(
                    """UPDATE bonus_questions
                       SET points_value=6,
                           scoring_mode='closest_podium',
                           scoring_config='{"preset_key":"fun_balanced","award_mode":"podium_custom","tie_policy":"full_skip","rank_points":[6,4,2],"min_value":0,"max_value":6}',
                           correct_answer='3'
                       WHERE id=?""",
                    (qid,),
                )
                await db.execute(
                    """INSERT INTO bonus_answers (participant_id, question_id, answer)
                       VALUES (?,?,?)
                       ON CONFLICT(participant_id, question_id)
                       DO UPDATE SET answer=excluded.answer""",
                    (right["id"], qid, "3"),
                )
                await db.execute(
                    """INSERT INTO bonus_answers (participant_id, question_id, answer)
                       VALUES (?,?,?)
                       ON CONFLICT(participant_id, question_id)
                       DO UPDATE SET answer=excluded.answer""",
                    (wrong["id"], qid, "4"),
                )
                await db.execute(
                    "DELETE FROM scores WHERE bonus_question_id=?",
                    (qid,),
                )
                await db.execute(
                    "INSERT INTO scores (participant_id, bonus_question_id, points) VALUES (?,?,6)",
                    (right["id"], qid),
                )
                await db.execute(
                    "INSERT INTO scores (participant_id, bonus_question_id, points) VALUES (?,?,6)",
                    (wrong["id"], qid),
                )
            await ensure_round_of_32_bonus_drafts(db)
            await db.commit()

    try:
        run(_downgrade_then_migrate())

        for text in (ROUND32_TIEBREAK_COUNT_TEXT, ROUND32_FAVORITE_CONCEDE_TEXT):
            q = _fetch_question_by_text(text)
            assert q["points_value"] == ROUND32_EXACT_POINTS
            assert q["scoring_mode"] == "exact"
            assert run(_score(right["id"], q["id"])) == ROUND32_EXACT_POINTS
            assert run(_score(wrong["id"], q["id"])) == 0
    finally:
        _delete_participants([right["id"], wrong["id"]])


def test_bonus_rejects_answer_after_deadline(client, participant):
    q = _fetch_question_by_text("Le Favori Qui Tremble")
    assert q is not None
    _publish_question(q["id"], "2000-01-01T00:00:00")  # already past
    resp = client.post(
        f"/p/{participant['token']}/bonus/{q['id']}",
        data={"answer": "2"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert run(_stored_answer(participant["id"], q["id"])) is None


async def _stored_answer(pid, qid):
    async with get_db() as db:
        row = await db.execute(
            "SELECT answer FROM bonus_answers WHERE participant_id=? AND question_id=?",
            (pid, qid),
        )
        r = await row.fetchone()
        return r["answer"] if r else None


async def _score(pid, qid):
    async with get_db() as db:
        row = await db.execute(
            "SELECT points FROM scores WHERE participant_id=? AND bonus_question_id=?",
            (pid, qid),
        )
        r = await row.fetchone()
        return r["points"] if r else None
