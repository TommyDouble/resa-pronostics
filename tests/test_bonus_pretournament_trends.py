"""Tendances collègues sur les 5 cartes pré-tournoi (B7b).

Réutilise le même modèle que les tendances des questions bonus classiques
(B7a/B7a.1, cf. tests/test_bonus_peer_answers.py) : top 3 groupes + mon
groupe, seuil d'anonymat, <details> local/global sans doublon. Confidentialité
non négociable : aucune tendance tant que la deadline pré-tournoi n'est pas
passée, et la source ne doit jamais lire correct_answer ni
pre_tournament_scores (seulement pre_tournament_predictions).
"""
import inspect
import uuid

from app.database import get_db
from app.routers import pages as pages_module
from tests.conftest import run

_PAST = "2020-01-01T12:00:00"
_FUTURE = "2035-01-01T12:00:00"

_GLOBAL_MARKER = "Voir les autres réponses"
_PT_KEYS = ("winner", "finalist", "top_scorer", "revelation", "total_goals")


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


def _seed_pt_prediction(participant_id, *, submitted=1, **overrides):
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
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(participant_id) DO UPDATE SET
                     winner=excluded.winner,
                     finalist=excluded.finalist,
                     top_scorer=excluded.top_scorer,
                     revelation=excluded.revelation,
                     total_goals=excluded.total_goals,
                     submitted=excluded.submitted,
                     submitted_at=excluded.submitted_at""",
                (
                    participant_id,
                    values["winner"],
                    values["finalist"],
                    values["top_scorer"],
                    values["revelation"],
                    values["total_goals"],
                    submitted,
                    _PAST,
                ),
            )
            await db.commit()

    run(_create())


def _set_pt_deadline(value):
    """Fixe la deadline PT et renvoie l'ancienne valeur (None si absente, pour
    restauration à l'identique : app_settings.value est NOT NULL, donc une
    valeur absente se restaure par suppression, pas par un upsert à NULL)."""
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


def _cleanup(participant_ids=(), pt_prediction_participants=()):
    async def _clean():
        async with get_db() as db:
            for pid in pt_prediction_participants:
                await db.execute(
                    "DELETE FROM pre_tournament_predictions WHERE participant_id=?",
                    (pid,),
                )
            if participant_ids:
                marks = ",".join("?" for _ in participant_ids)
                await db.execute(
                    f"DELETE FROM participants WHERE id IN ({marks})", participant_ids
                )
            await db.commit()

    run(_clean())


def _pt_card_html(html, key):
    """Isole le HTML d'une carte pré-tournoi (borne de fin : la prochaine
    carte pré-tournoi ou une carte de question bonus classique)."""
    marker = f'data-bonus-pt-key="{key}"'
    idx = html.index(marker)
    start = html.rindex("data-bonus-pt-card", 0, idx)
    ends = [
        e for e in (
            html.find("data-bonus-pt-card", idx + len(marker)),
            html.find("bonus-question-card", idx + len(marker)),
        )
        if e != -1
    ]
    end = min(ends) if ends else len(html)
    return html[start:end]


def _preview_html(card):
    start = card.index("pred-groups-head")
    marker_idx = card.find(_GLOBAL_MARKER, start)
    if marker_idx == -1:
        return card[start:]
    details_idx = card.rindex("<details", start, marker_idx)
    return card[start:details_idx]


def _global_remaining_html(card):
    marker_idx = card.find(_GLOBAL_MARKER)
    if marker_idx == -1:
        return None
    start = card.rindex("<details", 0, marker_idx)
    return card[start:]


# ---------------------------------------------------------------------------
# Confidentialité : rien avant la deadline, tout après
# ---------------------------------------------------------------------------

def test_open_pre_tournament_hides_all_trends(client, participant):
    """1. Deadline pré-tournoi ouverte : aucune tendance sur les 5 cartes."""
    old_deadline = _set_pt_deadline(_FUTURE)
    colleague_name = "Colette PT Ouvert"
    colleague_id = _seed_participant(colleague_name)
    _seed_pt_prediction(colleague_id)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        assert colleague_name not in html
        for key in _PT_KEYS:
            card = _pt_card_html(html, key)
            assert "Tendance collègues" not in card
            assert "%" not in card
            assert "· toi" not in card
    finally:
        _set_pt_deadline(old_deadline)
        _cleanup([colleague_id], [colleague_id])


def test_locked_pre_tournament_shows_trends(client, participant):
    """2. Deadline passée : la tendance apparaît sur les cartes."""
    colleagues = [_seed_participant(n) for n in ("Coll PT Verrou 1", "Coll PT Verrou 2")]
    for cid in colleagues:
        _seed_pt_prediction(cid)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        for key in _PT_KEYS:
            card = _pt_card_html(html, key)
            assert "Tendance collègues" in card
    finally:
        _cleanup(colleagues, colleagues)


def test_unsubmitted_prediction_excluded_from_trends(client, participant):
    """Une prédiction enregistrée mais non soumise (submitted=0) ne doit
    jamais apparaître dans les tendances collègues, même après la deadline —
    seules les prédictions réellement soumises (submitted=1) sont prises en
    compte."""
    draft_name = "Colette Brouillon Non Soumis"
    draft_id = _seed_participant(draft_name)
    _seed_pt_prediction(draft_id, submitted=0, winner="Portugal", revelation="Iran")
    submitted_id = _seed_participant("Coll Soumis PT")
    _seed_pt_prediction(submitted_id, submitted=1, winner="Espagne", revelation="Ghana")
    colleagues = [draft_id, submitted_id]
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        winner_card = _pt_card_html(html, "winner")
        revelation_card = _pt_card_html(html, "revelation")
        assert draft_name not in html
        assert "Portugal" not in winner_card
        assert "Iran" not in revelation_card
        assert "Espagne" in winner_card
        assert "Ghana" in revelation_card
    finally:
        _cleanup(colleagues, colleagues)


# ---------------------------------------------------------------------------
# Groupage par question
# ---------------------------------------------------------------------------

def test_champion_top_three_with_percentages(client, participant):
    """3. Champion : top 3 groupes + pourcentages corrects."""
    colleagues = []
    try:
        for name in ("Coll Champ F1", "Coll Champ F2", "Coll Champ F3"):
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_pt_prediction(cid, winner="France")
        for name in ("Coll Champ B1", "Coll Champ B2"):
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_pt_prediction(cid, winner="Brésil")
        _seed_pt_prediction(participant["id"], winner="Argentine")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "winner")
        preview = _preview_html(card)
        assert "France" in preview
        assert "Brésil" in preview
        assert "Argentine" in preview
        assert "50%" in preview  # 3/6
        assert "33%" in preview  # 2/6
    finally:
        _cleanup(colleagues, colleagues + [participant["id"]])


def test_finalists_merge_regardless_of_order(client, participant):
    """4. Finalistes : France+Brésil et Brésil+France fusionnent."""
    colleagues = []
    try:
        c1 = _seed_participant("Coll Finaliste Ordre 1")
        c2 = _seed_participant("Coll Finaliste Ordre 2")
        colleagues = [c1, c2]
        _seed_pt_prediction(c1, winner="France", finalist="Brésil")
        _seed_pt_prediction(c2, winner="Brésil", finalist="France")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "finalist")
        preview = _preview_html(card)
        assert preview.count('<section class="pgroup') == 1
        assert "2 joueurs" in preview
        assert "Brésil" in preview and "France" in preview
    finally:
        _cleanup(colleagues, colleagues)


def test_top_scorer_applies_normalize_scorer(client, participant):
    """5. Buteur : normalize_scorer() fusionne forme brute et canonique."""
    colleagues = []
    try:
        c1 = _seed_participant("Coll Buteur Brut")
        c2 = _seed_participant("Coll Buteur Canonique")
        colleagues = [c1, c2]
        _seed_pt_prediction(c1, top_scorer="Kylian Mbappé")
        _seed_pt_prediction(c2, top_scorer="Kylian Mbappé (France)")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "top_scorer")
        preview = _preview_html(card)
        assert preview.count('<section class="pgroup') == 1
        assert "2 joueurs" in preview
        assert "Kylian Mbappé (France)" in preview
    finally:
        _cleanup(colleagues, colleagues)


def test_revelation_groups_by_trimmed_value(client, participant):
    """6. Révélation : groupage par valeur textuelle trimée."""
    colleagues = []
    try:
        c1 = _seed_participant("Coll Revelation Trim 1")
        c2 = _seed_participant("Coll Revelation Trim 2")
        colleagues = [c1, c2]
        _seed_pt_prediction(c1, revelation="Maroc")
        _seed_pt_prediction(c2, revelation="  Maroc  ")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "revelation")
        preview = _preview_html(card)
        assert preview.count('<section class="pgroup') == 1
        assert "2 joueurs" in preview
    finally:
        _cleanup(colleagues, colleagues)


def test_total_goals_groups_by_exact_integer(client, participant):
    """7. Buts : groupage par valeur entière exacte."""
    colleagues = []
    try:
        c1 = _seed_participant("Coll Buts Exact 1")
        c2 = _seed_participant("Coll Buts Exact 2")
        c3 = _seed_participant("Coll Buts Exact 3")
        colleagues = [c1, c2, c3]
        _seed_pt_prediction(c1, total_goals=140)
        _seed_pt_prediction(c2, total_goals=140)
        _seed_pt_prediction(c3, total_goals=155)

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "total_goals")
        preview = _preview_html(card)
        assert "140" in preview
        assert "155" in preview
        # Le groupe "140" (2 réponses identiques) ne doit pas être scindé.
        section_140_idx = preview.index("140")
        assert preview[section_140_idx:section_140_idx + 200].count("joueur") >= 1
        assert "2 joueurs" in preview
    finally:
        _cleanup(colleagues, colleagues)


# ---------------------------------------------------------------------------
# Mon groupe
# ---------------------------------------------------------------------------

def test_my_group_added_when_outside_top_three(client, participant):
    """8. Mon groupe hors top 3 : ajouté au preview quand même."""
    colleagues = []
    try:
        for name, winner in [
            ("Coll Hors3 A1", "Alpha"), ("Coll Hors3 A2", "Alpha"), ("Coll Hors3 A3", "Alpha"),
            ("Coll Hors3 B1", "Bravo"), ("Coll Hors3 B2", "Bravo"),
            ("Coll Hors3 C1", "Charlie"),
        ]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_pt_prediction(cid, winner=winner)
        _seed_pt_prediction(participant["id"], winner="Delta")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "winner")
        preview = _preview_html(card)
        remaining = _global_remaining_html(card)

        assert "Alpha" in preview
        assert "Bravo" in preview
        assert "Charlie" in preview
        assert "Delta" in preview  # mon groupe, hors top 3 par comptage
        assert "· toi" in preview
        assert remaining is None  # rien d'autre hors aperçu (Charlie et Delta y sont)
    finally:
        _cleanup(colleagues, colleagues + [participant["id"]])


def test_my_group_in_top_three_shows_toi(client, participant):
    """9. Mon groupe dans le top 3 : "· toi" visible."""
    colleagues = []
    try:
        for name in ("Coll Toi 1", "Coll Toi 2"):
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_pt_prediction(cid, revelation="Maroc")
        _seed_pt_prediction(participant["id"], revelation="Maroc")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "revelation")
        preview = _preview_html(card)
        assert "· toi" in preview
    finally:
        _cleanup(colleagues, colleagues + [participant["id"]])


def test_singleton_group_not_mine_hides_name(client, participant):
    """10. Groupe singleton non-moi : aucun nom, pas de "Voir 1 autre"."""
    colleague_name = "Colette Solo PT"
    colleague_id = _seed_participant(colleague_name)
    _seed_pt_prediction(colleague_id, revelation="Australie")
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "revelation")
        preview = _preview_html(card)
        assert "Australie" in preview
        assert colleague_name not in card
        assert "Voir 1 autre" not in preview
        assert "+1" not in preview
    finally:
        _cleanup([colleague_id], [colleague_id])


def test_massive_group_shows_local_reveal(client, participant):
    """11. Groupe massif : "Voir les X autres" en détail local."""
    colleagues = []
    try:
        for i in range(22):
            cid = _seed_participant(f"Coll Massif PT {i:02d}")
            colleagues.append(cid)
            _seed_pt_prediction(cid, winner="France")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "winner")
        preview = _preview_html(card)
        assert "Voir les 18 autres" in preview
        assert "+18" not in preview
    finally:
        _cleanup(colleagues, colleagues)


# ---------------------------------------------------------------------------
# Bloc global
# ---------------------------------------------------------------------------

def test_global_block_excludes_preview_groups(client, participant):
    """12a. Le bloc global ne duplique pas les groupes déjà en aperçu."""
    colleagues = []
    try:
        for name, winner in [
            ("Coll GDup A1", "Alpha"), ("Coll GDup A2", "Alpha"), ("Coll GDup A3", "Alpha"),
            ("Coll GDup B1", "Bravo"), ("Coll GDup B2", "Bravo"),
            ("Coll GDup C1", "Charlie"),
            ("Coll GDup D1", "Delta"), ("Coll GDup D2", "Delta"),
        ]:
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_pt_prediction(cid, winner=winner)

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "winner")
        preview = _preview_html(card)
        remaining = _global_remaining_html(card)

        # Top 3 : Alpha(3), Bravo(2), Delta(2, après Bravo alphabétiquement).
        assert "Alpha" in preview
        assert "Bravo" in preview
        assert "Delta" in preview
        assert "Charlie" not in preview

        assert remaining is not None
        assert "Charlie" in remaining
        assert "Alpha" not in remaining
        assert "Bravo" not in remaining
        assert "Delta" not in remaining
    finally:
        _cleanup(colleagues, colleagues)


def test_global_block_absent_when_everything_fits_preview(client, participant):
    """12b. Bloc global absent quand tous les groupes tiennent dans l'aperçu."""
    colleagues = []
    try:
        for name in ("Coll GAbs 1", "Coll GAbs 2"):
            cid = _seed_participant(name)
            colleagues.append(cid)
            _seed_pt_prediction(cid, revelation="Japon")
        _seed_pt_prediction(participant["id"], revelation="Iran")

        html = client.get(f"/p/{participant['token']}/bonus").text
        card = _pt_card_html(html, "revelation")
        assert _GLOBAL_MARKER not in card
        assert _global_remaining_html(card) is None
    finally:
        _cleanup(colleagues, colleagues + [participant["id"]])


# ---------------------------------------------------------------------------
# Confidentialité de la source (pas de lecture de correct_answer / scores)
# ---------------------------------------------------------------------------

def test_loader_never_touches_correct_answer_or_scores_tables():
    """13. La requête ne lit jamais pre_tournament_questions (correct_answer)
    ni pre_tournament_scores : uniquement pre_tournament_predictions."""
    source = inspect.getsource(pages_module._load_pre_tournament_peer_trends)
    assert "pre_tournament_scores" not in source
    assert "pre_tournament_questions" not in source
    assert "pre_tournament_predictions" in source
