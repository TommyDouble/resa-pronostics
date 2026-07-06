"""Hub /bonus (PR-B4) : sections par statut, sous-groupes par étape,
pré-tournoi intégré comme carte-catégorie.

La BDD de test est partagée entre fichiers : textes uniques + cleanup, et
slicing du HTML par data-bonus-section plutôt que des positions globales.
Règle de confidentialité (PO) : une question encore ouverte reste « à
répondre », jamais présentée comme résolue, même si un score existe déjà.
"""
import uuid

from app.database import get_db
from app.routers.pages import _build_bonus_hub
from tests.conftest import run

_PAST = "2020-01-01T12:00:00"
_PAST_OLDER = "2019-01-01T12:00:00"
_FUTURE = "2035-01-01T12:00:00"
_FUTURE_LATER = "2036-01-01T12:00:00"


def _section_html(html, key):
    """Extrait le HTML d'une section du hub (jusqu'à la section suivante)."""
    marker = f'data-bonus-section="{key}"'
    start = html.index(marker)
    nxt = html.find("data-bonus-section=", start + len(marker))
    return html[start:nxt if nxt != -1 else len(html)]


def _seed_question(text, *, deadline, phase="group", answer_type="choice",
                   options='["Oui","Non"]'):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value, deadline)
                   VALUES (?, ?, ?, ?, 5, ?)""",
                (text, phase, answer_type, options, deadline),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _seed_score(question_id, participant_id, points):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO scores (participant_id, bonus_question_id, points)
                   VALUES (?, ?, ?)""",
                (participant_id, question_id, points),
            )
            await db.commit()

    run(_create())


def _seed_pt_score(participant_id, question_key, points):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO pre_tournament_scores (participant_id, question_key, points)
                   VALUES (?, ?, ?)""",
                (participant_id, question_key, points),
            )
            await db.commit()

    run(_create())


def _seed_participant(name):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed)
                   VALUES (?, ?, ?, 1)""",
                (name, f"{token}@test.local", token),
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


def _cleanup(question_ids=(), participant_ids=(), pt_scores_participant=None):
    async def _clean():
        async with get_db() as db:
            if question_ids:
                marks = ",".join("?" for _ in question_ids)
                await db.execute(
                    f"DELETE FROM scores WHERE bonus_question_id IN ({marks})",
                    question_ids,
                )
                await db.execute(
                    f"DELETE FROM bonus_answers WHERE question_id IN ({marks})",
                    question_ids,
                )
                await db.execute(
                    f"DELETE FROM bonus_questions WHERE id IN ({marks})", question_ids
                )
            if participant_ids:
                marks = ",".join("?" for _ in participant_ids)
                await db.execute(
                    f"DELETE FROM participants WHERE id IN ({marks})", participant_ids
                )
            if pt_scores_participant is not None:
                await db.execute(
                    "DELETE FROM pre_tournament_scores WHERE participant_id=?",
                    (pt_scores_participant,),
                )
            await db.commit()

    run(_clean())


def _set_pt_deadline(value):
    """Fixe la deadline PT et renvoie l'ancienne valeur (None si absente,
    d'autres tests supprimant parfois la clé sans la recréer)."""
    async def _set():
        async with get_db() as db:
            row = await db.execute(
                "SELECT value FROM app_settings WHERE key='pre_tournament_deadline'"
            )
            old_row = await row.fetchone()
            if value is None:
                await db.execute(
                    "DELETE FROM app_settings WHERE key='pre_tournament_deadline'"
                )
            else:
                await db.execute(
                    """INSERT INTO app_settings (key, value)
                       VALUES ('pre_tournament_deadline', ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    (value,),
                )
            await db.commit()
            return old_row["value"] if old_row else None

    return run(_set())


# ---------------------------------------------------------------------------
# Page : classification par statut
# ---------------------------------------------------------------------------

def test_status_sections_classification(client, participant):
    q_open = _seed_question("Question hub ouverte (hub-test) ?", deadline=_FUTURE)
    q_wait = _seed_question("Question hub en attente (hub-test) ?", deadline=_PAST)
    q_done = _seed_question("Question hub résolue (hub-test) ?", deadline=_PAST)
    _seed_score(q_done, participant["id"], 5)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        waiting_html = _section_html(html, "waiting")
        resolved_html = _section_html(html, "resolved")

        assert "Question hub ouverte (hub-test) ?" in open_html
        assert "Question hub ouverte (hub-test) ?" not in waiting_html
        assert "Question hub ouverte (hub-test) ?" not in resolved_html

        assert "Question hub en attente (hub-test) ?" in waiting_html
        assert "Question hub en attente (hub-test) ?" not in open_html
        assert "Question hub en attente (hub-test) ?" not in resolved_html

        assert "Question hub résolue (hub-test) ?" in resolved_html
        assert "Question hub résolue (hub-test) ?" not in open_html
        assert "Question hub résolue (hub-test) ?" not in waiting_html
    finally:
        _cleanup([q_open, q_wait, q_done])


def test_open_section_orders_by_closest_deadline(client, participant):
    q_late = _seed_question("Question hub tardive (hub-test) ?", deadline=_FUTURE_LATER)
    q_soon = _seed_question("Question hub imminente (hub-test) ?", deadline=_FUTURE)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        assert open_html.index("Question hub imminente (hub-test) ?") < \
            open_html.index("Question hub tardive (hub-test) ?")
    finally:
        _cleanup([q_soon, q_late])


def test_resolved_subgroups_order_and_subtotals(client, participant):
    q_final = _seed_question("Question hub finale (hub-test) ?", deadline=_PAST, phase="final")
    q_group1 = _seed_question("Question hub groupes A (hub-test) ?", deadline=_PAST)
    q_group2 = _seed_question("Question hub groupes B (hub-test) ?", deadline=_PAST_OLDER)
    _seed_score(q_final, participant["id"], 3)
    _seed_score(q_group1, participant["id"], 2)
    _seed_score(q_group2, participant["id"], 4)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        resolved_html = _section_html(html, "resolved")
        # Étapes du plus récent au plus ancien : finale avant groupes.
        assert resolved_html.index('data-bonus-phase="final"') < \
            resolved_html.index('data-bonus-phase="group"')
        # Sous-totaux par étape (somme des points déjà calculés, rien de recalculé).
        assert 'data-bonus-phase-points="3"' in resolved_html
        assert 'data-bonus-phase-points="6"' in resolved_html
        # Dans un groupe : deadline la plus récente d'abord.
        assert resolved_html.index("Question hub groupes A (hub-test) ?") < \
            resolved_html.index("Question hub groupes B (hub-test) ?")
    finally:
        _cleanup([q_final, q_group1, q_group2])


def test_waiting_multi_phase_shows_subheaders(client, participant):
    q_semi = _seed_question("Question hub demi attente (hub-test) ?", deadline=_PAST, phase="semi")
    q_group = _seed_question("Question hub groupe attente (hub-test) ?", deadline=_PAST)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        waiting_html = _section_html(html, "waiting")
        assert 'data-bonus-phase="semi"' in waiting_html
        assert 'data-bonus-phase="group"' in waiting_html
        # Ordre chronologique inversé : demi avant groupes.
        assert waiting_html.index('data-bonus-phase="semi"') < \
            waiting_html.index('data-bonus-phase="group"')
    finally:
        _cleanup([q_semi, q_group])


# ---------------------------------------------------------------------------
# Carte-catégorie pré-tournoi
# ---------------------------------------------------------------------------

def test_pt_card_waiting_by_default(client, participant):
    """Deadline PT passée, aucun score : la carte PT est « en attente »."""
    html = client.get(f"/p/{participant['token']}/bonus").text
    waiting_html = _section_html(html, "waiting")
    assert "data-bonus-pt-card" in waiting_html
    assert "Verrouillée" in waiting_html
    assert f"/p/{participant['token']}/pre-tournoi" in waiting_html


def test_pt_card_resolved_with_points_in_subtotal(client, participant):
    _seed_pt_score(participant["id"], "winner", 8)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        resolved_html = _section_html(html, "resolved")
        assert "data-bonus-pt-card" in resolved_html
        assert 'data-bonus-phase="pre_tournament"' in resolved_html
        assert 'data-bonus-phase-points="8"' in resolved_html
        assert f"/p/{participant['token']}/pre-tournoi" in resolved_html
        # Et plus dans « en attente » (section absente si vide).
        if 'data-bonus-section="waiting"' in html:
            assert "data-bonus-pt-card" not in _section_html(html, "waiting")
    finally:
        _cleanup(pt_scores_participant=participant["id"])


def test_pt_card_open_when_deadline_future(client, participant):
    old_deadline = _set_pt_deadline(_FUTURE)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        assert "data-bonus-pt-card" in open_html
        assert "À répondre ·" in open_html
        assert "Compléter mes réponses" in open_html
    finally:
        _set_pt_deadline(old_deadline)


# ---------------------------------------------------------------------------
# Résumé et invariants B1–B3
# ---------------------------------------------------------------------------

def test_summary_counters_and_locked_labels(client, participant):
    html = client.get(f"/p/{participant['token']}/bonus").text
    assert "data-bonus-counters" in html
    assert "à répondre ·" in html
    # Libellés de la réconciliation (PR #61) inchangés.
    assert "Total bonus :" in html
    assert "Questions bonus :" in html
    assert "Pré-tournoi :" in html


def test_open_scored_question_stays_private(client, participant):
    """P1 : ouverte + scorée/résolue par accident → classée « À répondre »
    (is_open prime), mais sans révéler ni la bonne réponse ni le score obtenu.
    Bonne réponse distinctive et score (3) distinct du barème (5 pts)."""
    q_id = _seed_question("Question hub ouverte scorée (hub-test) ?", deadline=_FUTURE)

    async def _resolve_early():
        async with get_db() as db:
            await db.execute(
                "UPDATE bonus_questions SET correct_answer=? WHERE id=?",
                ("reponse-correcte-distinctive-hub", q_id),
            )
            await db.commit()

    run(_resolve_early())
    _seed_score(q_id, participant["id"], 3)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        assert "Question hub ouverte scorée (hub-test) ?" in open_html
        # La bonne réponse ne fuit nulle part sur la page.
        assert "reponse-correcte-distinctive-hub" not in html
        # Isoler la carte de cette question dans la section ouverte.
        marker = "bonus-question-card"
        idx = open_html.index("Question hub ouverte scorée (hub-test) ?")
        start = open_html.rindex(marker, 0, idx)
        end = open_html.find(marker, idx)
        card = open_html[start:end if end != -1 else len(open_html)]
        assert "Réponse correcte" not in card
        assert "3 pt" not in card  # le score obtenu n'est pas affiché
    finally:
        _cleanup([q_id])


def test_no_peer_answers_in_open_section(client, participant):
    """Confidentialité B3 au niveau section : rien ne fuite pour une ouverte."""
    colleague_name = "Colette Collègue Hub"
    q_open = _seed_question(
        "Question hub confidentielle (hub-test) ?", deadline=_FUTURE,
        answer_type="text", options=None,
    )
    colleague_id = _seed_participant(colleague_name)
    _seed_answer(q_open, colleague_id, "reponse-secrete-hub")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        assert colleague_name not in html
        assert "reponse-secrete-hub" not in html
        open_html = _section_html(html, "open")
        assert "Réponses des collègues" not in open_html
    finally:
        _cleanup([q_open], [colleague_id])


# ---------------------------------------------------------------------------
# Unitaires : _build_bonus_hub (fonction pure)
# ---------------------------------------------------------------------------

def test_build_bonus_hub_empty():
    hub = _build_bonus_hub([], None)
    assert hub["open"] == []
    assert hub["waiting"] == {"count": 0, "groups": []}
    assert hub["resolved"] == {"count": 0, "groups": []}
    assert hub["counts"] == {"open": 0, "waiting": 0, "resolved": 0}


def test_build_bonus_hub_open_wins_over_score():
    """Confidentialité : ouverte + scorée par accident → reste « à répondre »,
    et sa version d'affichage est neutralisée (rien du résultat ne fuit)."""
    q = {"is_open": True, "has_score": True, "phase": "final", "points": 3,
         "correct_answer": "secret", "correct_answer_display": "secret",
         "deadline": _FUTURE, "answer_type": "choice", "answer": "Oui",
         "has_answer": True, "can_edit": False}
    hub = _build_bonus_hub([q])
    assert hub["resolved"]["count"] == 0
    (item,) = hub["open"]
    assert item["has_score"] is False
    assert item["points"] is None
    assert item["correct_answer"] is None
    assert item["correct_answer_display"] is None
    # Les champs non liés au résultat sont préservés.
    assert item["deadline"] == _FUTURE
    assert item["answer_type"] == "choice"
    assert item["answer"] == "Oui"
    assert item["has_answer"] is True
    assert item["can_edit"] is False
    # Copie défensive : l'original du loader n'est pas muté.
    assert q["has_score"] is True
    assert q["correct_answer"] == "secret"

    # Cas minimal : seul correct_answer_display existe → neutralisé aussi.
    q_display_only = {
        "is_open": True,
        "has_score": False,
        "phase": "final",
        "points": None,
        "correct_answer": None,
        "correct_answer_display": "secret-display",
    }
    hub = _build_bonus_hub([q_display_only])
    (item,) = hub["open"]
    assert item["correct_answer_display"] is None
    assert q_display_only["correct_answer_display"] == "secret-display"


def test_build_bonus_hub_phase_order_and_points():
    questions = [
        {"is_open": False, "has_score": True, "phase": "group",
         "points": 2, "deadline": _PAST},
        {"is_open": False, "has_score": True, "phase": "final",
         "points": None, "deadline": _PAST},
        {"is_open": False, "has_score": True, "phase": "group",
         "points": 4, "deadline": _PAST_OLDER},
    ]
    hub = _build_bonus_hub(questions)
    groups = hub["resolved"]["groups"]
    assert [g["phase"] for g in groups] == ["final", "group"]
    assert groups[0]["points"] == 0  # points None → 0, pas d'erreur
    assert groups[1]["points"] == 6
    # Dans un groupe : deadline la plus récente d'abord.
    assert [q["points"] for q in groups[1]["entries"]] == [2, 4]


def test_build_bonus_hub_pt_card_placement():
    pt_waiting = {"is_pre_tournament": True, "phase": "pre_tournament",
                  "is_open": False, "has_score": False, "points": 0}
    q_pt_phase = {"is_open": False, "has_score": False,
                  "phase": "pre_tournament", "deadline": _PAST}
    hub = _build_bonus_hub([q_pt_phase], pt_waiting)
    group = hub["waiting"]["groups"][0]
    assert group["phase"] == "pre_tournament"
    # La carte PT ouvre son sous-groupe, devant les questions de même phase.
    assert group["entries"][0] is pt_waiting
    assert group["entries"][1] is q_pt_phase

    pt_resolved = {"is_pre_tournament": True, "phase": "pre_tournament",
                   "is_open": False, "has_score": True, "points": 8}
    hub = _build_bonus_hub([], pt_resolved)
    assert hub["resolved"]["count"] == 1
    assert hub["resolved"]["groups"][0]["points"] == 8

    pt_open = {"is_pre_tournament": True, "phase": "pre_tournament",
               "is_open": True, "has_score": False, "points": 0}
    q_open = {"is_open": True, "has_score": False, "phase": "final",
              "deadline": _FUTURE}
    hub = _build_bonus_hub([q_open], pt_open)
    assert hub["open"][0] is pt_open
    assert hub["open"][1] is q_open
