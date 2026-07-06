"""Tendances des réponses des collègues sur /bonus (PR-B3, tendances B7a).

Politique : privé tant que la question est ouverte, public dès que la deadline
est passée. Le test critique vérifie la source HTML : aucune réponse tierce ni
bloc « Tendance collègues » ne doit exister pour une question ouverte.

Depuis B7a, l'affichage brut est remplacé par une lecture de tendances : top 3
groupes + mon groupe (ajouté s'il est hors top 3) en aperçu, avec un seuil
d'anonymat (un groupe de 1 personne qui n'est pas moi ne montre aucun nom en
aperçu), et le détail complet (tous les groupes, tous les noms) toujours
disponible dans un <details> "Voir toutes les réponses".
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


def _preview_html(card):
    """Isole le bloc d'aperçu (top 3 + mon groupe), avant le <details> replié."""
    start = card.index("pred-groups-head")
    details_idx = card.find("<details", start)
    end = details_idx if details_idx != -1 else len(card)
    return card[start:end]


def _details_html(card):
    """Isole le contenu du <details> "Voir toutes les réponses"."""
    start = card.index("<details", card.index("pred-groups-head"))
    return card[start:]


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
        assert "Tendance collègues" not in card
        assert "Personne n'a répondu" not in card
        assert "%" not in card
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
        assert "Tendance collègues" in card
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
        assert "Tendance collègues" in card
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
        assert "Tendance collègues" in card
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
        assert "Tendance collègues" in card
        assert admin_name not in html
    finally:
        _cleanup([question_id], [admin_id])


# ---------------------------------------------------------------------------
# B7a — tendances : top 3, mon groupe, seuil d'anonymat, pourcentages
# ---------------------------------------------------------------------------

def test_preview_shows_top_three_groups(client, participant):
    """Top 3 groupes en aperçu : le 4e groupe (hors top 3, pas le mien) reste
    caché de l'aperçu et n'apparaît que dans le <details> complet."""
    question_id = _seed_question(
        "Question top3 (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["Alpha","Bravo","Charlie","Delta","Echo"]',
    )
    colleagues = []
    try:
        # Alpha: 3, Bravo: 2, Charlie: 1, Delta: 1 (aucun n'est "moi")
        for name, answer in [
            ("Coll Alpha 1", "Alpha"), ("Coll Alpha 2", "Alpha"), ("Coll Alpha 3", "Alpha"),
            ("Coll Bravo 1", "Bravo"), ("Coll Bravo 2", "Bravo"),
            ("Coll Charlie 1", "Charlie"),
            ("Coll Delta 1", "Delta"),
        ]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, answer)
        # Moi : Echo (5e valeur distincte, hors top 3 par comptage)
        _seed_answer(question_id, participant["id"], "Echo")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question top3 (peer-test) ?")
        preview = _preview_html(card)
        details = _details_html(card)

        assert "Alpha" in preview
        assert "Bravo" in preview
        assert "Charlie" in preview  # 1er des ex-æquo à 1 (ordre alphabétique)
        assert "Delta" not in preview  # 4e ex-æquo, hors top 3, pas mon groupe
        assert "Echo" in preview  # mon groupe, ajouté même hors top 3

        assert "Delta" in details  # toujours visible dans le détail complet
    finally:
        _cleanup([question_id], colleagues)


def test_my_group_appended_when_outside_top_three(client, participant):
    """Mon groupe est ajouté à l'aperçu même s'il n'est pas dans le top 3."""
    question_id = _seed_question(
        "Question mon-groupe (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["Alpha","Bravo","Charlie","Delta"]',
    )
    colleagues = []
    try:
        for name, answer in [
            ("Coll A", "Alpha"), ("Coll A2", "Alpha"),
            ("Coll B", "Bravo"), ("Coll B2", "Bravo"),
            ("Coll C", "Charlie"), ("Coll C2", "Charlie"),
        ]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, answer)
        _seed_answer(question_id, participant["id"], "Delta")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question mon-groupe (peer-test) ?")
        preview = _preview_html(card)
        assert "Delta" in preview
        assert "· toi" in preview
    finally:
        _cleanup([question_id], colleagues)


def test_percentage_is_computed_correctly(client, participant):
    """5 réponses au total, un groupe de 3 → 60%."""
    question_id = _seed_question(
        "Question pourcentage (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["France","Brésil"]',
    )
    colleagues = []
    try:
        for name in ["Coll P1", "Coll P2", "Coll P3"]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, "France")
        for name in ["Coll P4"]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, "Brésil")
        _seed_answer(question_id, participant["id"], "Brésil")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question pourcentage (peer-test) ?")
        assert "60%" in card  # 3 réponses France sur 5 au total
    finally:
        _cleanup([question_id], colleagues)


def test_group_of_one_hides_name_when_not_mine(client, participant):
    """Un groupe de 1 personne (pas moi) ne montre aucun nom en aperçu."""
    colleague_name = "Colette Solo Anonyme"
    question_id = _seed_question(
        "Question solo anonyme (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["France","Brésil","Maroc"]',
    )
    colleague_id = _seed_participant(colleague_name)
    _seed_answer(question_id, colleague_id, "Maroc")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question solo anonyme (peer-test) ?")
        preview = _preview_html(card)
        # Le libellé de la réponse reste visible (Maroc), mais pas le nom.
        assert "Maroc" in preview
        assert colleague_name not in preview
        # Ni le nom masqué, ni un badge "+1" ne doivent fuiter le fait qu'il y
        # a bien 1 personne cachée derrière ce groupe en aperçu.
        assert "+1" not in preview
        # Le détail complet, lui, montre bien le nom.
        details = _details_html(card)
        assert colleague_name in details
    finally:
        _cleanup([question_id], [colleague_id])


def test_group_of_one_shows_toi_when_mine(client, participant):
    """Un groupe de 1 personne qui est moi affiche "· toi" en aperçu."""
    question_id = _seed_question(
        "Question solo moi (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["France","Brésil","Maroc"]',
    )
    _seed_answer(question_id, participant["id"], "Maroc")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question solo moi (peer-test) ?")
        preview = _preview_html(card)
        assert "Maroc" in preview
        assert "· toi" in preview
    finally:
        _cleanup([question_id])


def test_details_lists_every_group_including_singletons(client, participant):
    """Le <details> complet reste exhaustif, y compris pour les groupes de 1."""
    colleague_name = "Colette Solo Détail"
    question_id = _seed_question(
        "Question détail complet (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["France","Brésil"]',
    )
    colleague_id = _seed_participant(colleague_name)
    _seed_answer(question_id, colleague_id, "Brésil")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question détail complet (peer-test) ?")
        details = _details_html(card)
        assert colleague_name in details
        assert "Voir toutes les réponses" in card
    finally:
        _cleanup([question_id], [colleague_id])


def test_number_answers_merge_synonymous_values(client, participant):
    """"3" et "3,0" désignent la même réponse numérique : un seul groupe."""
    question_id = _seed_question(
        "Question nombre synonyme (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="number",
        options=None,
    )
    colleagues = []
    try:
        c1 = _seed_participant("Coll Nombre 1")
        c2 = _seed_participant("Coll Nombre 2")
        colleagues = [c1, c2]
        _seed_answer(question_id, c1, "3")
        _seed_answer(question_id, c2, "3,0")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question nombre synonyme (peer-test) ?")
        preview = _preview_html(card)
        assert preview.count('<section class="pgroup') == 1
        assert "2 joueurs" in preview
    finally:
        _cleanup([question_id], colleagues)
