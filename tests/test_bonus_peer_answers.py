"""Réponses des collègues sur /bonus (PR-B3).

Politique : privé tant que la question est ouverte, public dès que la deadline
est passée. Le test critique vérifie la source HTML : aucune réponse tierce ni
bloc « Réponses des collègues » ne doit exister pour une question ouverte.
"""
import uuid

from app.database import get_db
from tests.conftest import run

_PAST_DEADLINE = "2020-01-01T12:00:00"
_FUTURE_DEADLINE = "2035-01-01T12:00:00"


def _card_html(html, question_text):
    """Extrait le HTML de la carte d'une question (la page peut contenir des
    questions laissées par d'autres tests, la BDD étant partagée)."""
    marker = "bonus-question-card"
    idx = html.index(question_text)
    start = html.rindex(marker, 0, idx)
    end = html.find(marker, idx)
    return html[start:end if end != -1 else len(html)]


def _seed_question(question_text, *, deadline, answer_type="choice",
                   options='["France","Brésil"]'):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value, deadline)
                   VALUES (?, 'pre_tournament', ?, ?, 5, ?)""",
                (question_text, answer_type, options, deadline),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _seed_participant(name, *, is_admin=0):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants
                   (name, email, token, is_confirmed, is_admin)
                   VALUES (?, ?, ?, 1, ?)""",
                (name, f"{token}@test.local", token, is_admin),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _seed_answer(question_id, participant_id, answer):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO bonus_answers (participant_id, question_id, answer)
                   VALUES (?, ?, ?)""",
                (participant_id, question_id, answer),
            )
            await db.commit()

    run(_create())


def _cleanup(question_ids, participant_ids=()):
    async def _clean():
        async with get_db() as db:
            q_marks = ",".join("?" for _ in question_ids)
            await db.execute(
                f"DELETE FROM bonus_answers WHERE question_id IN ({q_marks})",
                question_ids,
            )
            await db.execute(
                f"DELETE FROM bonus_questions WHERE id IN ({q_marks})", question_ids
            )
            if participant_ids:
                p_marks = ",".join("?" for _ in participant_ids)
                await db.execute(
                    f"DELETE FROM participants WHERE id IN ({p_marks})",
                    participant_ids,
                )
            await db.commit()

    run(_clean())


def test_open_question_hides_peer_answers(client, participant):
    """Critique : question ouverte → aucune réponse tierce dans le HTML."""
    colleague_name = "Colette Collègue Ouverte"
    colleague_answer = "reponse-secrete-avant-deadline"
    question_id = _seed_question(
        "Question ouverte (peer-test) ?",
        deadline=_FUTURE_DEADLINE,
        answer_type="text",
        options=None,
    )
    colleague_id = _seed_participant(colleague_name)
    _seed_answer(question_id, colleague_id, colleague_answer)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        # Sur toute la page : ni le nom du collègue ni sa réponse ne fuitent.
        assert colleague_name not in html
        assert colleague_answer not in html
        # Dans la carte de la question ouverte : aucun bloc communautaire.
        card = _card_html(html, "Question ouverte (peer-test) ?")
        assert "Réponses des collègues" not in card
        assert "Personne n'a répondu" not in card
    finally:
        _cleanup([question_id], [colleague_id])


def test_locked_question_shows_peer_answers(client, participant):
    colleague_name = "Colette Collègue Verrouillée"
    question_id = _seed_question(
        "Question verrouillée (peer-test) ?", deadline=_PAST_DEADLINE
    )
    colleague_id = _seed_participant(colleague_name)
    _seed_answer(question_id, colleague_id, "France")
    _seed_answer(question_id, participant["id"], "Brésil")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question verrouillée (peer-test) ?")
        assert "Réponses des collègues" in card
        assert colleague_name in card
        assert "France" in card
        assert "· toi" in card
    finally:
        _cleanup([question_id], [colleague_id])


def test_locked_question_no_answers_empty_state(client, participant):
    question_id = _seed_question(
        "Question verrouillée sans réponse (peer-test) ?", deadline=_PAST_DEADLINE
    )
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question verrouillée sans réponse (peer-test) ?")
        assert "Réponses des collègues" in card
        assert "Personne n'a répondu" in card
    finally:
        _cleanup([question_id])


def test_locked_multi_choice_formats_answer(client, participant):
    colleague_name = "Colette Collègue Multi"
    question_id = _seed_question(
        "Question multi verrouillée (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="multi_choice",
    )
    colleague_id = _seed_participant(colleague_name)
    _seed_answer(question_id, colleague_id, '["France", "Brésil"]')
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question multi verrouillée (peer-test) ?")
        assert "Réponses des collègues" in card
        assert colleague_name in card
        assert "Brésil, France" in card  # format_team_list trie alphabétiquement
    finally:
        _cleanup([question_id], [colleague_id])


def test_admin_participant_excluded(client, participant):
    admin_name = "Arsène Adminverrou"
    question_id = _seed_question(
        "Question verrouillée admin exclu (peer-test) ?", deadline=_PAST_DEADLINE
    )
    admin_id = _seed_participant(admin_name, is_admin=1)
    _seed_answer(question_id, admin_id, "France")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question verrouillée admin exclu (peer-test) ?")
        assert "Réponses des collègues" in card
        assert admin_name not in html
    finally:
        _cleanup([question_id], [admin_id])
