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
_PT_KEYS = ("winner", "finalist", "top_scorer", "revelation", "total_goals")


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


def _seed_pt_prediction(participant_id, **overrides):
    values = {
        "winner": "France",
        "finalist": "Brésil",
        "top_scorer": "Kylian Mbappé",
        "revelation": "Maroc",
        "total_goals": 140,
    }
    values.update(overrides)

    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO pre_tournament_predictions
                   (participant_id, winner, finalist, top_scorer, revelation,
                    total_goals, submitted, submitted_at)
                   VALUES (?,?,?,?,?,?,1,?)
                   ON CONFLICT(participant_id) DO UPDATE SET
                     winner=excluded.winner,
                     finalist=excluded.finalist,
                     top_scorer=excluded.top_scorer,
                     revelation=excluded.revelation,
                     total_goals=excluded.total_goals,
                     submitted=1,
                     submitted_at=excluded.submitted_at""",
                (
                    participant_id,
                    values["winner"],
                    values["finalist"],
                    values["top_scorer"],
                    values["revelation"],
                    values["total_goals"],
                    _PAST,
                ),
            )
            await db.commit()

    run(_create())


def _set_pt_correct_answer(question_key, value):
    async def _set():
        async with get_db() as db:
            row = await db.execute(
                "SELECT correct_answer FROM pre_tournament_questions WHERE key=?",
                (question_key,),
            )
            old_row = await row.fetchone()
            await db.execute(
                "UPDATE pre_tournament_questions SET correct_answer=? WHERE key=?",
                (value, question_key),
            )
            await db.commit()
            return old_row["correct_answer"] if old_row else None

    return run(_set())


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


def _cleanup(question_ids=(), participant_ids=(), pt_scores_participant=None,
             pt_prediction_participant=None):
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
            if pt_prediction_participant is not None:
                await db.execute(
                    "DELETE FROM pre_tournament_predictions WHERE participant_id=?",
                    (pt_prediction_participant,),
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


def _assert_pt_keys(section_html):
    for key in _PT_KEYS:
        assert f'data-bonus-pt-key="{key}"' in section_html


def _pt_card_html(section_html, key):
    marker = f'data-bonus-pt-key="{key}"'
    idx = section_html.index(marker)
    start = section_html.rindex("data-bonus-pt-card", 0, idx)
    end = section_html.find("data-bonus-pt-card", idx + len(marker))
    return section_html[start:end if end != -1 else len(section_html)]


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
# Cartes pré-tournoi
# ---------------------------------------------------------------------------

def test_pt_card_waiting_by_default(client, participant):
    """Deadline PT passée, aucun score : les 5 cartes PT sont « en attente »."""
    html = client.get(f"/p/{participant['token']}/bonus").text
    waiting_html = _section_html(html, "waiting")
    assert waiting_html.count("data-bonus-pt-card") == 5
    _assert_pt_keys(waiting_html)
    assert "Non répondue" in waiting_html
    for key in _PT_KEYS:
        assert f"/p/{participant['token']}/pre-tournoi#pt-{key}" in waiting_html


def test_pt_card_resolved_with_points_in_subtotal(client, participant):
    _seed_pt_score(participant["id"], "winner", 8)
    old_answer = _set_pt_correct_answer("winner", "France")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        resolved_html = _section_html(html, "resolved")
        assert "data-bonus-pt-card" in resolved_html
        assert 'data-bonus-pt-key="winner"' in resolved_html
        assert 'data-bonus-phase="pre_tournament"' in resolved_html
        assert 'data-bonus-phase-points="8"' in resolved_html
        assert f"/p/{participant['token']}/pre-tournoi#pt-winner" in resolved_html
        assert "Réponse correcte" in resolved_html
        assert "France" in resolved_html
        assert "Total bonus : 8 pts" in html
        assert "Pré-tournoi : 8 pts" in html
        # La carte scorée n'est plus dans « en attente » ; les autres clés y restent.
        if 'data-bonus-section="waiting"' in html:
            assert 'data-bonus-pt-key="winner"' not in _section_html(html, "waiting")
    finally:
        _set_pt_correct_answer("winner", old_answer)
        _cleanup(pt_scores_participant=participant["id"])


def test_pt_card_open_when_deadline_future(client, participant):
    old_deadline = _set_pt_deadline(_FUTURE)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        assert open_html.count("data-bonus-pt-card") == 5
        _assert_pt_keys(open_html)
        assert "À répondre" in open_html
        assert "Compléter cette réponse" in open_html
    finally:
        _set_pt_deadline(old_deadline)


def test_pt_finalists_card_requires_both_picks_when_open(client, participant):
    old_deadline = _set_pt_deadline(_FUTURE)
    _seed_pt_prediction(
        participant["id"],
        winner="France",
        finalist="",
        top_scorer="",
        revelation="",
        total_goals=0,
    )
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        winner_card = _pt_card_html(open_html, "winner")
        finalist_card = _pt_card_html(open_html, "finalist")

        assert "Réponse enregistrée" in winner_card
        assert "Ta réponse : <b>France</b>" in winner_card
        assert "À répondre" in finalist_card
        assert "Réponse enregistrée" not in finalist_card
        assert "Ta réponse : <b>France</b>" not in finalist_card
    finally:
        _set_pt_deadline(old_deadline)
        _cleanup(pt_prediction_participant=participant["id"])


def test_pt_card_resolved_zero_points_is_still_scored(client, participant):
    old_answer = _set_pt_correct_answer("winner", "France")
    _seed_pt_score(participant["id"], "winner", 0)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        resolved_html = _section_html(html, "resolved")
        assert 'data-bonus-pt-key="winner"' in resolved_html
        assert "0 pt" in resolved_html
        assert "Réponse correcte" in resolved_html
    finally:
        _set_pt_correct_answer("winner", old_answer)
        _cleanup(pt_scores_participant=participant["id"])


def test_pt_card_open_hides_accidental_score_and_correct_answer(client, participant):
    old_deadline = _set_pt_deadline(_FUTURE)
    old_answer = _set_pt_correct_answer("winner", "secret-champion-b5")
    _seed_pt_score(participant["id"], "winner", 3)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        assert 'data-bonus-pt-key="winner"' in open_html
        assert "secret-champion-b5" not in html
        assert "Réponse correcte" not in html
        assert "3 pt" not in html
        assert "Pré-tournoi : 3 pts" not in html
        assert "Total bonus : 3 pts" not in html
        assert "Pré-tournoi : 0 pt" in html
        assert "Total bonus : 0 pt" in html
        if 'data-bonus-section="resolved"' in html:
            assert 'data-bonus-pt-key="winner"' not in _section_html(html, "resolved")
    finally:
        _set_pt_correct_answer("winner", old_answer)
        _set_pt_deadline(old_deadline)
        _cleanup(pt_scores_participant=participant["id"])


def test_pre_tournament_page_hides_accidental_score_when_open(client, participant):
    old_deadline = _set_pt_deadline(_FUTURE)
    _seed_pt_score(participant["id"], "winner", 3)
    try:
        html = client.get(f"/p/{participant['token']}/pre-tournoi").text
        assert "Tes points pré-tournoi" not in html
        assert "3 pt" not in html
        assert "Verrouillé" not in html
        assert "Enregistrer mes pronos" in html
        assert 'name="winner"' in html
        start = html.index('id="pt-winner"')
        end = html.index('id="pt-finalist"', start)
        assert "disabled" not in html[start:end]
    finally:
        _set_pt_deadline(old_deadline)
        _cleanup(pt_scores_participant=participant["id"])


def test_pt_scores_visible_after_deadline_on_bonus_and_pre_tournament(client, participant):
    old_deadline = _set_pt_deadline(_PAST)
    _seed_pt_score(participant["id"], "winner", 3)
    try:
        bonus_html = client.get(f"/p/{participant['token']}/bonus").text
        pre_tournament_html = client.get(f"/p/{participant['token']}/pre-tournoi").text

        resolved_html = _section_html(bonus_html, "resolved")
        assert 'data-bonus-pt-key="winner"' in resolved_html
        assert "3 pts" in resolved_html
        assert "Pré-tournoi : 3 pts" in bonus_html
        assert "Total bonus : 3 pts" in bonus_html

        assert "Tes points pré-tournoi" in pre_tournament_html
        assert "Champion du Monde · 3 pts" in pre_tournament_html
        assert "Total · 3 pts" in pre_tournament_html
        assert "Verrouillé" in pre_tournament_html
    finally:
        _set_pt_deadline(old_deadline)
        _cleanup(pt_scores_participant=participant["id"])


def test_pre_tournament_page_shows_zero_score_after_deadline(client, participant):
    old_deadline = _set_pt_deadline(_PAST)
    _seed_pt_score(participant["id"], "winner", 0)
    try:
        html = client.get(f"/p/{participant['token']}/pre-tournoi").text
        assert "Tes points pré-tournoi" in html
        assert "Champion du Monde · 0 pt" in html
        assert "Total · 0 pts" in html
    finally:
        _set_pt_deadline(old_deadline)
        _cleanup(pt_scores_participant=participant["id"])


def test_pt_card_displays_business_answers(client, participant):
    _seed_pt_prediction(
        participant["id"],
        winner="Argentine",
        finalist="France",
        top_scorer="Kylian Mbappé",
        revelation="Maroc",
        total_goals=151,
    )
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        waiting_html = _section_html(html, "waiting")
        assert "Champion" in waiting_html
        assert "Ta réponse : <b>Argentine</b>" in waiting_html
        assert "Finalistes" in waiting_html
        assert "Ta réponse : <b>Argentine + France</b>" in waiting_html
        assert "Buteur" in waiting_html
        assert "Ta réponse : <b>Kylian Mbappé" in waiting_html
        assert "Révélation" in waiting_html
        assert "Ta réponse : <b>Maroc</b>" in waiting_html
        assert "Buts" in waiting_html
        assert "Ta réponse : <b>151</b>" in waiting_html
    finally:
        _cleanup(pt_prediction_participant=participant["id"])


def test_pre_tournament_page_exposes_pt_anchors(client, participant):
    html = client.get(f"/p/{participant['token']}/pre-tournoi").text
    for key in _PT_KEYS:
        assert f'id="pt-{key}"' in html


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
