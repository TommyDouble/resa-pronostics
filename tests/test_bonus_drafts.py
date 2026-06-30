from html import unescape
import json
import uuid

from app.database import ensure_bonus_question_drafts, get_db
from tests.conftest import run


J3_GOALS = "Feu d'artifice J3 - Combien de buts seront marqués sur les 24 matchs de la troisième journée des groupes ?"
J3_POPCORN = "Match popcorn J3 - Y aura-t-il au moins un match avec 5 buts ou plus pendant la troisième journée des groupes ?"


def _bonus_question_by_text(text):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM bonus_questions WHERE question_text=?", (text,)
            )
            item = await row.fetchone()
            return dict(item) if item else None

    return run(_get())


def _ensure_j3_drafts():
    async def _ensure():
        async with get_db() as db:
            await db.execute(
                "DELETE FROM app_settings WHERE key='bonus_drafts_group_j3_2026_v1'"
            )
            await ensure_bonus_question_drafts(db)
            await db.commit()

    run(_ensure())


def _delete_bonus(question_id):
    async def _delete():
        async with get_db() as db:
            await db.execute("DELETE FROM bonus_questions WHERE id=?", (question_id,))
            await db.commit()

    run(_delete())


def _add_bonus_answer(question_id, name, answer):
    token = str(uuid.uuid4())

    async def _add():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed)
                   VALUES (?,?,?,1)""",
                (name, f"{token}@test.local", token),
            )
            participant_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO bonus_answers (participant_id, question_id, answer)
                   VALUES (?,?,?)""",
                (participant_id, question_id, answer),
            )
            await db.commit()
            return participant_id

    return run(_add())


def _insert_bonus_question(text, *, deadline, is_published=1, correct_answer=None):
    async def _insert():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value,
                    correct_answer, scoring_mode, is_published, deadline)
                   VALUES (?, 'group', 'choice', '["Oui","Non"]', 6, ?, 'exact', ?, ?)""",
                (text, correct_answer, is_published, deadline),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_insert())


def test_seeded_j3_bonus_drafts_visible_admin_hidden_participant(admin_client, participant):
    _ensure_j3_drafts()
    admin_html = unescape(admin_client.get("/admin/bonus").text)
    participant_html = unescape(admin_client.get(f"/p/{participant['token']}/bonus").text)

    assert J3_GOALS in admin_html
    assert J3_POPCORN in admin_html
    assert "Brouillon" in admin_html
    assert "Recommandé pour une question fun" in admin_html
    assert "Preset de répartition" in admin_html
    assert "Enregistrer le brouillon" in admin_html
    assert "Aperçu participant" in admin_html
    assert "Miniature non interactive du rendu côté participant" in admin_html
    assert "Plein palier, rangs sautés" in admin_html
    assert J3_GOALS not in participant_html
    assert J3_POPCORN not in participant_html

    goals = _bonus_question_by_text(J3_GOALS)
    assert goals["phase"] == "group"
    assert goals["answer_type"] == "number"
    assert goals["scoring_mode"] == "closest_podium"
    assert json.loads(goals["scoring_config"]) == {
        "preset_key": "fun_balanced",
        "award_mode": "podium_custom",
        "tie_policy": "full_skip",
        "rank_points": [6, 4, 2],
    }
    assert goals["is_published"] == 0


def test_admin_can_prepare_draft_then_publish(admin_client):
    title = f"Question brouillon {uuid.uuid4()}"
    response = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": title,
            "phase": "group",
            "answer_type": "choice",
            "points_value": "6",
            "options_text": "Oui\nNon",
            "deadline": "2035-01-01T12:00",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    question = _bonus_question_by_text(title)
    assert question["is_published"] == 0

    try:
        response = admin_client.post(
            f"/admin/bonus/{question['id']}/update",
            data={
                "question_text": title,
                "phase": "group",
                "answer_type": "choice",
                "points_value": "6",
                "options_text": "Oui\nNon",
                "deadline": "2035-01-01T12:00",
                "is_published": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        question = _bonus_question_by_text(title)
        assert question["is_published"] == 1
    finally:
        _delete_bonus(question["id"])


def test_admin_bonus_uses_compact_status_accordions(admin_client):
    draft = f"Bonus brouillon UI {uuid.uuid4()}"
    open_q = f"Bonus ouvert UI {uuid.uuid4()}"
    to_score = f"Bonus à corriger UI {uuid.uuid4()}"
    scored = f"Bonus scoré UI {uuid.uuid4()}"
    ids = [
        _insert_bonus_question(draft, deadline="2035-01-01T12:00:00", is_published=0),
        _insert_bonus_question(open_q, deadline="2035-01-02T12:00:00"),
        _insert_bonus_question(to_score, deadline="2020-01-01T12:00:00"),
        _insert_bonus_question(scored, deadline="2020-01-02T12:00:00", correct_answer="Oui"),
    ]

    try:
        html = unescape(admin_client.get("/admin/bonus").text)

        assert "data-admin-bonus-controls" in html
        assert "data-admin-bonus-filter=\"draft\"" in html
        assert "data-admin-bonus-filter=\"open\"" in html
        assert "data-admin-bonus-filter=\"to_score\"" in html
        assert "data-admin-bonus-filter=\"scored\"" in html
        assert "admin-bonus-summary" in html
        assert "data-status=\"draft\"" in html
        assert "data-status=\"open\"" in html
        assert "data-status=\"to_score\"" in html
        assert "data-status=\"scored\"" in html
        assert "Brouillon" in html
        assert "Ouverte" in html
        assert "À corriger" in html
        assert "Scorée" in html
        assert "répondu" in html
        assert "Aperçu participant" in html

        draft_start = html.index('data-status="draft"')
        to_score_start = html.index('data-status="to_score"')
        open_start = html.index('data-status="open"')
        draft_tag = html[draft_start:html.index(">", draft_start)]
        to_score_tag = html[to_score_start:html.index(">", to_score_start)]
        open_tag = html[open_start:html.index(">", open_start)]
        assert " open" in draft_tag
        assert " open" in to_score_tag
        assert " open" not in open_tag
    finally:
        for question_id in ids:
            _delete_bonus(question_id)


def test_unpublished_bonus_cannot_be_submitted_directly(admin_client, participant):
    _ensure_j3_drafts()
    draft = _bonus_question_by_text(J3_POPCORN)

    response = admin_client.post(
        f"/p/{participant['token']}/bonus/{draft['id']}",
        data={"answer": "Oui"},
        follow_redirects=False,
    )

    assert response.status_code == 404


def test_admin_can_configure_closest_points_and_preview_standings(admin_client):
    title = f"Question closest {uuid.uuid4()}"
    response = admin_client.post(
        "/admin/bonus/create",
        data={
            "question_text": title,
            "phase": "group",
            "answer_type": "number",
            "points_value": "6",
            "deadline": "2035-01-01T12:00",
            "closest_preset_key": "fun_balanced",
            "closest_award_mode": "podium_custom",
            "closest_tie_policy": "full_skip",
            "closest_rank1_points": "6",
            "closest_rank2_points": "4",
            "closest_rank3_points": "2",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    question = _bonus_question_by_text(title)
    _add_bonus_answer(question["id"], "Alice Closest", "60")
    _add_bonus_answer(question["id"], "Bob Closest", "59")
    _add_bonus_answer(question["id"], "Carla Closest", "61")

    try:
        response = admin_client.post(
            f"/admin/bonus/{question['id']}/update",
            data={
                "question_text": title,
                "phase": "group",
                "points_value": "6",
                "deadline": "2035-01-01T12:00",
                "correct_answer": "60",
                "closest_preset_key": "custom",
                "closest_award_mode": "winner_takes_all",
                "closest_tie_policy": "full_skip",
                "closest_rank1_points": "9",
                "closest_rank2_points": "4",
                "closest_rank3_points": "2",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        question = _bonus_question_by_text(title)
        assert question["points_value"] == 9
        assert json.loads(question["scoring_config"]) == {
            "preset_key": "custom",
            "award_mode": "winner_takes_all",
            "tie_policy": "full_skip",
            "rank_points": [9, 0, 0],
        }

        html = unescape(admin_client.get("/admin/bonus").text)
        fragment = html[html.index(title):]
        assert "Classement de cette question" in fragment
        assert "#1" in fragment and "Alice Closest" in fragment
        assert "#2" in fragment and "Bob Closest" in fragment and "Carla Closest" in fragment
        assert "Winner takes all" in fragment
    finally:
        _delete_bonus(question["id"])
