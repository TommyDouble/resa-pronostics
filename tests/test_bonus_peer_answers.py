"""Tendances des réponses des collègues sur /bonus (PR-B3, tendances B7a/B7a.1).

Politique : privé tant que la question est ouverte, public dès que la deadline
est passée. Le test critique vérifie la source HTML : aucune réponse tierce ni
bloc « Tendance collègues » ne doit exister pour une question ouverte.

Depuis B7a, l'affichage brut est remplacé par une lecture de tendances : top 3
groupes + mon groupe (ajouté s'il est hors top 3) en aperçu, avec un seuil
d'anonymat (un groupe de 1 personne qui n'est pas moi ne montre aucun nom en
aperçu, ni de moyen de le révéler).

Depuis B7a.1 :
- chaque groupe d'aperçu garantit que "moi" figure dans ses noms d'exemple
  (quitte à remplacer le dernier exemple non-moi) ;
- un groupe d'aperçu qui a plus de membres que les exemples affichés propose
  un <details> LOCAL "Voir les X autres" (plus de badge "+N" muet) ;
- le bloc global replié ("Voir les autres réponses") ne contient plus que les
  groupes hors aperçu, pour ne pas dupliquer le top 3 déjà visible.
"""
import json
import re
import uuid

from app.database import get_db
from tests.conftest import run

_PAST_DEADLINE = "2020-01-01T12:00:00"
_FUTURE_DEADLINE = "2035-01-01T12:00:00"

_GLOBAL_MARKER = "Voir les autres réponses"


def _card_html(html, question_text):
    """Extrait le HTML de la carte d'une question (la page peut contenir des
    questions laissées par d'autres tests, la BDD étant partagée).

    La borne de fin doit s'arrêter à la prochaine carte, qu'il s'agisse d'une
    autre question bonus ou d'une carte pré-tournoi (B7b) : sinon, une fois
    la deadline pré-tournoi passée, le bloc "Tendance collègues" des cartes
    pré-tournoi qui suivent se retrouve inclus dans la carte extraite.
    """
    marker = "bonus-question-card"
    idx = html.index(question_text)
    start = html.rindex(marker, 0, idx)
    ends = [e for e in (html.find(marker, idx), html.find("bonus-pt-card", idx)) if e != -1]
    end = min(ends) if ends else len(html)
    return html[start:end]


def _preview_html(card):
    """Isole le bloc d'aperçu (top 3 + mon groupe), avant le bloc global replié.

    Les <details> LOCAUX ("Voir les X autres" d'un groupe précis) restent
    inclus : ils font partie de l'aperçu. Seul le bloc global ("Voir les
    autres réponses", hors aperçu) est exclu.
    """
    start = card.index("pred-groups-head")
    marker_idx = card.find(_GLOBAL_MARKER, start)
    if marker_idx == -1:
        return card[start:]
    details_idx = card.rindex("<details", start, marker_idx)
    return card[start:details_idx]


def _global_remaining_html(card):
    """Isole le contenu du bloc global "Voir les autres réponses" (hors aperçu),
    ou None si ce bloc n'existe pas (aucun groupe hors aperçu)."""
    marker_idx = card.find(_GLOBAL_MARKER)
    if marker_idx == -1:
        return None
    start = card.rindex("<details", 0, marker_idx)
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
    """Un groupe collègue de taille ≥2 est visible ; mon propre choix aussi."""
    question_id = _seed_question(
        "Question verrouillée (peer-test) ?", deadline=_PAST_DEADLINE
    )
    colleagues = []
    for name in ["Colette Collègue Verrouillée", "Camille Collègue Verrouillée"]:
        cid = _seed_participant(name)
        colleagues.append(cid)
        _seed_answer(question_id, cid, "France")
    _seed_answer(question_id, participant["id"], "Brésil")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question verrouillée (peer-test) ?")
        assert "Tendance collègues" in card
        assert "Colette Collègue Verrouillée" in card
        assert "France" in card
        assert "· toi" in card
    finally:
        _cleanup([question_id], colleagues)


def test_resolved_question_shows_scorers_once_and_zero_scores_collapsed(
    client, participant
):
    question_id = _seed_question(
        "Question gagnants visibles (peer-test) ?", deadline=_PAST_DEADLINE
    )
    colleagues = []
    scored_answers = [
        ("Aline Gagnante", "France", 5),
        ("Basile Gagnant", "Brésil", 3),
        ("Chloé Sans Point", "Espagne", 0),
    ]
    for name, answer, points in scored_answers:
        colleague_id = _seed_participant(name)
        colleagues.append(colleague_id)
        _seed_answer(question_id, colleague_id, answer)
        _seed_score(question_id, colleague_id, points)
    _seed_answer(question_id, participant["id"], "Espagne")
    _seed_score(question_id, participant["id"], 0)

    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question gagnants visibles (peer-test) ?")

        assert "data-bonus-score-breakdown" in card
        assert "Tendance collègues" not in card
        assert "Qui a marqué des points ?" in card
        assert "France" in card and "+5 pts" in card
        assert "Aline Gagnante" in card
        assert "Brésil" in card and "+3 pts" in card
        assert "Basile Gagnant" in card
        assert "data-bonus-zero-scores" in card
        assert "Voir 2 participants à 0 pt" in card
        assert "Espagne" in card and "Chloé Sans Point" in card
        zero_tag_start = card.index("data-bonus-zero-scores")
        zero_tag = card[card.rfind("<details", 0, zero_tag_start):card.index(">", zero_tag_start)]
        assert " open" not in zero_tag
        # Aucun participant n'est répété dans un second bloc de tendances.
        assert card.count("Aline Gagnante") == 1
        assert card.count("Basile Gagnant") == 1
        assert card.count("Chloé Sans Point") == 1
    finally:
        _cleanup([question_id], colleagues)


def test_resolved_question_is_visible_to_participant_without_answer_or_score(
    client, participant
):
    question_id = _seed_question(
        "Question résolue visible par tous (peer-test) ?", deadline=_PAST_DEADLINE
    )
    colleague_id = _seed_participant("Aline Résultat Global")
    _seed_answer(question_id, colleague_id, "France")
    _seed_score(question_id, colleague_id, 5)

    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question résolue visible par tous (peer-test) ?")

        assert "data-bonus-score-breakdown" in card
        assert "Tendance collègues" not in card
        assert "Aline Résultat Global" in card
        assert "+5 pts" in card
        assert "0 pt" in card  # score personnel affiché dans l'en-tête
        assert 'data-bonus-section="resolved"' in html
    finally:
        _cleanup([question_id], [colleague_id])


def test_resolved_numeric_answers_merge_equivalent_values(client, participant):
    question_id = _seed_question(
        "Question nombres équivalents (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="number",
        options=None,
    )
    colleagues = []
    for name, answer in (("Alice Nombre Trois", "3"), ("Basile Nombre Trois", "3,0")):
        colleague_id = _seed_participant(name)
        colleagues.append(colleague_id)
        _seed_answer(question_id, colleague_id, answer)
        _seed_score(question_id, colleague_id, 5)

    try:
        card = _card_html(
            client.get(f"/p/{participant['token']}/bonus").text,
            "Question nombres équivalents (peer-test) ?",
        )
        assert len(re.findall(r'class="pg-outcome">3(?:[.,]0)?</span>', card)) == 1
        assert card.count("Alice Nombre Trois") == 1
        assert card.count("Basile Nombre Trois") == 1
        assert "Tendance collègues" not in card
    finally:
        _cleanup([question_id], colleagues)


def test_resolved_minute_answers_keep_symmetric_values_separate(client, participant):
    question_id = _seed_question(
        "Question minutes symétriques (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="number",
        options=None,
    )

    async def _minute_config():
        async with get_db() as db:
            await db.execute(
                """UPDATE bonus_questions
                   SET scoring_mode='closest_podium',
                       scoring_config=?
                   WHERE id=?""",
                (json.dumps({
                    "award_mode": "podium_custom",
                    "tie_policy": "full_dense",
                    "rank_points": [5, 3, 1],
                    "minute_notation": True,
                }), question_id),
            )
            await db.commit()

    run(_minute_config())
    colleagues = []
    for name, answer in (("Alice Minute 119", "119"), ("Basile Minute 120+3", "120.03")):
        colleague_id = _seed_participant(name)
        colleagues.append(colleague_id)
        _seed_answer(question_id, colleague_id, answer)
        _seed_score(question_id, colleague_id, 1)

    try:
        card = _card_html(
            client.get(f"/p/{participant['token']}/bonus").text,
            "Question minutes symétriques (peer-test) ?",
        )
        assert len(re.findall(r'class="pg-outcome">119</span>', card)) == 1
        assert len(re.findall(r'class="pg-outcome">120\+3</span>', card)) == 1
        assert card.count("Alice Minute 119") == 1
        assert card.count("Basile Minute 120+3") == 1
    finally:
        _cleanup([question_id], colleagues)


def test_resolved_question_uses_latest_duplicate_score_once(client, participant):
    question_id = _seed_question(
        "Question correction score dupliqué (peer-test) ?", deadline=_PAST_DEADLINE
    )
    colleague_id = _seed_participant("Alice Score Corrigé")
    _seed_answer(question_id, colleague_id, "France")
    _seed_score(question_id, colleague_id, 5)
    _seed_score(question_id, colleague_id, 0)

    try:
        card = _card_html(
            client.get(f"/p/{participant['token']}/bonus").text,
            "Question correction score dupliqué (peer-test) ?",
        )
        assert card.count("Alice Score Corrigé") == 1
        assert "+5 pts" not in card
        assert "Voir 1 participant à 0 pt" in card
    finally:
        _cleanup([question_id], [colleague_id])


def test_open_question_never_shows_score_breakdown(client, participant):
    question_id = _seed_question(
        "Question gagnants encore secrets (peer-test) ?", deadline=_FUTURE_DEADLINE
    )
    colleague_id = _seed_participant("Gagnant Encore Secret")
    _seed_answer(question_id, colleague_id, "réponse-gagnante-secrète")
    _seed_score(question_id, colleague_id, 5)
    _seed_score(question_id, participant["id"], 0)

    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question gagnants encore secrets (peer-test) ?")
        assert "data-bonus-score-breakdown" not in card
        assert "Gagnant Encore Secret" not in html
        assert "réponse-gagnante-secrète" not in html
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
    """Groupe multi_choice de taille ≥2 : formatage + noms visibles."""
    question_id = _seed_question(
        "Question multi verrouillée (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="multi_choice",
    )
    colleagues = []
    for name in ["Colette Collègue Multi", "Camille Collègue Multi"]:
        cid = _seed_participant(name)
        colleagues.append(cid)
        _seed_answer(question_id, cid, '["France", "Brésil"]')
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question multi verrouillée (peer-test) ?")
        assert "Tendance collègues" in card
        assert "Colette Collègue Multi" in card
        assert "Brésil, France" in card  # format_team_list trie alphabétiquement
    finally:
        _cleanup([question_id], colleagues)


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
    caché de l'aperçu et n'apparaît que dans le bloc global hors aperçu."""
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
        remaining = _global_remaining_html(card)

        assert "Alpha" in preview
        assert "Bravo" in preview
        assert "Charlie" in preview  # 1er des ex-æquo à 1 (ordre alphabétique)
        assert "Delta" not in preview  # 4e ex-æquo, hors top 3, pas mon groupe
        assert "Echo" in preview  # mon groupe, ajouté même hors top 3

        assert remaining is not None
        assert "Delta" in remaining  # visible dans le bloc global hors aperçu
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
    """Un groupe de 1 personne (pas moi) ne montre ni nom ni "Voir 1 autre",
    nulle part sur la page (l'aperçu contient tous les groupes ici, donc pas
    de bloc global hors aperçu pour "rattraper" le nom masqué)."""
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
        assert colleague_name not in card
        assert "+1" not in preview
        assert "Voir 1 autre" not in preview
        # Un seul groupe au total : rien à afficher hors aperçu.
        assert _global_remaining_html(card) is None
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


def test_minute_notation_peer_trend_shows_readable_format(client, participant):
    """La tendance collègues affiche "90+3", jamais le décimal brut "90.03"."""
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, points_value, deadline,
                    scoring_mode, scoring_config)
                   VALUES (?, 'pre_tournament', 'number', 5, ?, 'closest_podium', ?)""",
                (
                    "Question minute notation (peer-test) ?",
                    _PAST_DEADLINE,
                    json.dumps({"min_value": 1, "max_value": 121, "minute_notation": True}),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    question_id = run(_create())
    colleagues = []
    try:
        c1 = _seed_participant("Coll Minute 1")
        colleagues = [c1]
        _seed_answer(question_id, c1, "90.03")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question minute notation (peer-test) ?")
        assert "90+3" in card
        assert "90.03" not in card
    finally:
        _cleanup([question_id], colleagues)


# ---------------------------------------------------------------------------
# B7a.1 — révélation locale "Voir les X autres", dédoublonnage du bloc global,
# garantie de présence de "moi" dans les exemples
# ---------------------------------------------------------------------------

def test_large_group_shows_local_reveal_with_correct_count(client, participant):
    """22 réponses dans un même groupe, 4 exemples affichés → "Voir les 18
    autres" en local, dans l'aperçu (pas de badge +N muet)."""
    question_id = _seed_question(
        "Question groupe massif (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["France","Brésil"]',
    )
    colleagues = []
    try:
        for i in range(22):
            cid = _seed_participant(f"Coll Massif {i:02d}")
            colleagues.append(cid)
            _seed_answer(question_id, cid, "France")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question groupe massif (peer-test) ?")
        preview = _preview_html(card)
        assert "Voir les 18 autres" in preview
        assert "+18" not in preview
    finally:
        _cleanup([question_id], colleagues)


def test_large_group_local_reveal_contains_the_remaining_names(client, participant):
    """Les 18 noms restants sont bien dans le <details> local du groupe."""
    question_id = _seed_question(
        "Question groupe massif détail (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["France","Brésil"]',
    )
    colleagues = []
    names = [f"Coll Massif Detail {i:02d}" for i in range(22)]
    try:
        for name in names:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, "France")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question groupe massif détail (peer-test) ?")
        preview = _preview_html(card)
        # Les 4 premiers (ordre alphabétique) sont des exemples directs.
        for name in sorted(names)[:4]:
            assert name in preview
        # Le 18e (dernier alphabétique) n'est pas un exemple direct, mais est
        # bien présent quelque part dans le <details> local du groupe.
        assert sorted(names)[-1] in preview
    finally:
        _cleanup([question_id], colleagues)


def test_global_block_excludes_preview_groups(client, participant):
    """Le bloc global "Voir les autres réponses" ne réaffiche pas les groupes
    déjà visibles en aperçu (pas de doublon)."""
    question_id = _seed_question(
        "Question sans doublon (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["Alpha","Bravo","Charlie","Delta"]',
    )
    colleagues = []
    try:
        for name, answer in [
            ("Coll Alpha 1", "Alpha"), ("Coll Alpha 2", "Alpha"), ("Coll Alpha 3", "Alpha"),
            ("Coll Bravo 1", "Bravo"), ("Coll Bravo 2", "Bravo"),
            ("Coll Charlie 1", "Charlie"),
            ("Coll Delta 1", "Delta"), ("Coll Delta 2", "Delta"),
        ]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, answer)

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question sans doublon (peer-test) ?")
        preview = _preview_html(card)
        remaining = _global_remaining_html(card)

        # Top 3 : Alpha(3), Bravo(2), Delta(2) [ex-æquo à 2, Bravo < Delta
        # alphabétiquement] ; Charlie(1) reste hors aperçu.
        assert "Alpha" in preview
        assert "Bravo" in preview
        assert "Delta" in preview
        assert "Charlie" not in preview

        assert remaining is not None
        assert "Charlie" in remaining
        # Alpha/Bravo/Delta ne doivent PAS être dupliqués dans le bloc global.
        assert "Alpha" not in remaining
        assert "Bravo" not in remaining
        assert "Delta" not in remaining
        # Charlie est un singleton non-moi hors aperçu : le libellé de
        # réponse reste visible dans le bloc global, mais pas le nom du
        # participant (seuil d'anonymat appliqué au bloc global aussi).
        assert "Coll Charlie 1" not in remaining
        assert "Voir 1 autre" not in remaining
    finally:
        _cleanup([question_id], colleagues)


def test_global_block_shows_names_for_two_person_group(client, participant):
    """Un groupe hors aperçu d'au moins 2 personnes (seuil d'anonymat atteint)
    affiche bien les noms dans le bloc global — seuls les singletons non-moi
    voient leur nom masqué."""
    question_id = _seed_question(
        "Question global deux personnes (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["Alpha","Bravo","Charlie","Delta"]',
    )
    colleagues = []
    try:
        for name, answer in [
            ("Coll Deux Alpha 1", "Alpha"), ("Coll Deux Alpha 2", "Alpha"), ("Coll Deux Alpha 3", "Alpha"),
            ("Coll Deux Bravo 1", "Bravo"), ("Coll Deux Bravo 2", "Bravo"), ("Coll Deux Bravo 3", "Bravo"),
            ("Coll Deux Charlie 1", "Charlie"), ("Coll Deux Charlie 2", "Charlie"), ("Coll Deux Charlie 3", "Charlie"),
            ("Coll Deux Delta 1", "Delta"), ("Coll Deux Delta 2", "Delta"),
        ]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, answer)

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question global deux personnes (peer-test) ?")
        preview = _preview_html(card)
        remaining = _global_remaining_html(card)

        # Top 3 ex-æquo à 3 : Alpha, Bravo, Charlie (ordre alphabétique).
        # Delta (2 personnes) reste hors aperçu, dans le bloc global.
        assert "Delta" not in preview
        assert remaining is not None
        assert "Delta" in remaining
        assert "Coll Deux Delta 1" in remaining
        assert "Coll Deux Delta 2" in remaining
    finally:
        _cleanup([question_id], colleagues)


def test_global_block_absent_when_everything_is_in_preview(client, participant):
    """Si tous les groupes tiennent dans l'aperçu (≤ 3, ou mon groupe ajouté),
    le bloc global "Voir les autres réponses" n'apparaît pas du tout."""
    question_id = _seed_question(
        "Question tout en aperçu (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["France","Brésil"]',
    )
    colleagues = []
    try:
        for name in ["Coll Unique 1", "Coll Unique 2"]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, "France")
        _seed_answer(question_id, participant["id"], "Brésil")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question tout en aperçu (peer-test) ?")
        assert _GLOBAL_MARKER not in card
        assert _global_remaining_html(card) is None
    finally:
        _cleanup([question_id], colleagues)


def test_my_name_appears_in_preview_even_if_not_among_first_raw_names(client, participant):
    """Mon groupe est dans le top 3, mais mon nom n'est pas dans les 4
    premiers noms bruts (ordre alphabétique) : il doit quand même apparaître
    dans les exemples, quitte à remplacer le dernier exemple non-moi."""
    question_id = _seed_question(
        "Question mon nom pas en tête (peer-test) ?",
        deadline=_PAST_DEADLINE,
        answer_type="choice",
        options='["France","Brésil"]',
    )
    colleagues = []
    try:
        # 5 collègues nommés pour être alphabétiquement avant "participant"
        # (le fixture 'participant' a un nom générique, on force un préfixe
        # qui le précède alphabétiquement en le nommant explicitement "Zzz").
        for name in ["Coll Aaa 1", "Coll Bbb 2", "Coll Ccc 3", "Coll Ddd 4", "Coll Eee 5"]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_answer(question_id, cid, "France")
        _seed_answer(question_id, participant["id"], "France")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _card_html(html, "Question mon nom pas en tête (peer-test) ?")
        preview = _preview_html(card)
        # Groupe unique (France, 6 réponses) : forcément dans le top 3.
        assert "· toi" in preview
        # Le 5e collègue alphabétique (normalement hors des 4 exemples
        # naturels) doit être rattrapable dans le <details> local.
        assert "Voir les" in preview
    finally:
        _cleanup([question_id], colleagues)


# ---------------------------------------------------------------------------
# B7a.1 — "Ma situation Bonus" : la barre "Prochaine action" ne change ni le
# texte ni les compteurs, seulement l'espacement.
# ---------------------------------------------------------------------------

def test_situation_block_texts_unchanged_by_cta_spacing_fix(client, participant):
    question_id = _seed_question(
        "Question situation espacement (peer-test) ?", deadline=_FUTURE_DEADLINE
    )
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        assert "Ma situation Bonus" in html
        assert "Total bonus" in html
        assert "Prochaine action" in html
        assert "bonus-next-action" in html
    finally:
        _cleanup([question_id])


def test_bonus_next_action_css_rule_is_scoped(client):
    """La règle CSS ajoutée pour l'espacement doit être limitée à la classe
    dédiée du bloc bonus, pas au composant partagé .home-cta-bonus global."""
    css = client.get("/static/css/resa.css").text
    assert ".bonus-next-action.home-cta-bonus" in css
    assert "padding-left" in css.split(".bonus-next-action.home-cta-bonus", 1)[1][:120]
