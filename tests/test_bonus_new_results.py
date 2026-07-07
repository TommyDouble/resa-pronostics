"""Indicateur "nouveau résultat bonus disponible" (PR-B8).

Un résultat (question bonus classique ou carte pré-tournoi) est "nouveau" si le
participant n'a jamais vu la version actuelle de son résultat (bonus_result_views,
comparaison par result_version = date de calcul du score). La visite de /bonus
marque les résultats visibles comme vus pour les PROCHAINES visites seulement :
les badges de la requête en cours ne sont jamais affectés par ce marquage.
"""
import uuid

from app.database import get_db, init_db
from tests.conftest import run

_PAST = "2020-01-01T12:00:00"
_PAST_RECALC = "2021-06-01T12:00:00"
_FUTURE = "2035-01-01T12:00:00"


def _section_html(html, key):
    marker = f'data-bonus-section="{key}"'
    start = html.index(marker)
    nxt = html.find("data-bonus-section=", start + len(marker))
    return html[start:nxt if nxt != -1 else len(html)]


def _pt_card_html(section_html, key):
    marker = f'data-bonus-pt-key="{key}"'
    idx = section_html.index(marker)
    start = section_html.rindex("data-bonus-pt-card", 0, idx)
    end = section_html.find("data-bonus-pt-card", idx + len(marker))
    return section_html[start:end if end != -1 else len(section_html)]


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


def _seed_score(question_id, participant_id, points, calculated_at=None):
    async def _create():
        async with get_db() as db:
            if calculated_at is None:
                await db.execute(
                    """INSERT INTO scores (participant_id, bonus_question_id, points)
                       VALUES (?, ?, ?)""",
                    (participant_id, question_id, points),
                )
            else:
                await db.execute(
                    """INSERT INTO scores
                       (participant_id, bonus_question_id, points, calculated_at)
                       VALUES (?, ?, ?, ?)""",
                    (participant_id, question_id, points, calculated_at),
                )
            await db.commit()

    run(_create())


def _update_score_calculated_at(question_id, participant_id, value):
    async def _update():
        async with get_db() as db:
            await db.execute(
                """UPDATE scores SET calculated_at=?
                   WHERE bonus_question_id=? AND participant_id=?""",
                (value, question_id, participant_id),
            )
            await db.commit()

    run(_update())


def _seed_pt_score(participant_id, question_key, points, calculated_at=None):
    async def _create():
        async with get_db() as db:
            if calculated_at is None:
                await db.execute(
                    """INSERT INTO pre_tournament_scores (participant_id, question_key, points)
                       VALUES (?, ?, ?)""",
                    (participant_id, question_key, points),
                )
            else:
                await db.execute(
                    """INSERT INTO pre_tournament_scores
                       (participant_id, question_key, points, calculated_at)
                       VALUES (?, ?, ?, ?)""",
                    (participant_id, question_key, points, calculated_at),
                )
            await db.commit()

    run(_create())


def _update_pt_score_calculated_at(participant_id, question_key, value):
    async def _update():
        async with get_db() as db:
            await db.execute(
                """UPDATE pre_tournament_scores SET calculated_at=?
                   WHERE participant_id=? AND question_key=?""",
                (value, participant_id, question_key),
            )
            await db.commit()

    run(_update())


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


def _set_pt_deadline(value):
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


def _bonus_result_views(participant_id):
    async def _get():
        async with get_db() as db:
            rows = await db.execute(
                """SELECT source, source_key, result_version
                   FROM bonus_result_views WHERE participant_id=?""",
                (participant_id,),
            )
            return [dict(r) for r in await rows.fetchall()]

    return run(_get())


def _cleanup(question_ids=(), participant_ids=(), pt_scores_participant=None,
             pt_prediction_participant=None):
    async def _clean():
        async with get_db() as db:
            if question_ids:
                marks = ",".join("?" for _ in question_ids)
                await db.execute(
                    f"""DELETE FROM bonus_result_views
                        WHERE source='bonus_question' AND source_key IN ({marks})""",
                    [str(q) for q in question_ids],
                )
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
                    "DELETE FROM bonus_result_views WHERE participant_id=? AND source='pre_tournament'",
                    (pt_scores_participant,),
                )
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


# ---------------------------------------------------------------------------
# 1-3 : question bonus classique — nouveau, vu, recalculé
# ---------------------------------------------------------------------------

def test_new_bonus_question_result_shows_badge_and_priority(client, participant):
    q = _seed_question("Question B8 nouvelle (b8-test) ?", deadline=_PAST)
    _seed_score(q, participant["id"], 5, calculated_at=_PAST)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        resolved_html = _section_html(html, "resolved")
        assert f'id="bonus-q-{q}"' in resolved_html
        assert "chip new" in resolved_html
        assert "Nouveau résultat" in resolved_html
        assert "1 nouveau résultat" in html
        assert 'data-bonus-next-action="new_result"' in html
        assert f'href="#bonus-q-{q}"' in html
    finally:
        _cleanup(question_ids=[q])


def test_bonus_question_badge_disappears_after_first_visit(client, participant):
    q = _seed_question("Question B8 déjà vue (b8-test) ?", deadline=_PAST)
    _seed_score(q, participant["id"], 5, calculated_at=_PAST)
    try:
        client.get(f"/p/{participant['token']}/bonus")
        html = client.get(f"/p/{participant['token']}/bonus").text
        resolved_html = _section_html(html, "resolved")
        assert "chip new" not in resolved_html
        assert "Nouveau résultat" not in resolved_html
        assert 'data-bonus-next-action="new_result"' not in html
    finally:
        _cleanup(question_ids=[q])


def test_bonus_question_badge_reappears_after_recalculation(client, participant):
    q = _seed_question("Question B8 recalculée (b8-test) ?", deadline=_PAST)
    _seed_score(q, participant["id"], 5, calculated_at=_PAST)
    try:
        client.get(f"/p/{participant['token']}/bonus")  # marque vu
        seen_html = client.get(f"/p/{participant['token']}/bonus").text
        assert "chip new" not in _section_html(seen_html, "resolved")

        _update_score_calculated_at(q, participant["id"], _PAST_RECALC)
        html = client.get(f"/p/{participant['token']}/bonus").text
        resolved_html = _section_html(html, "resolved")
        assert "chip new" in resolved_html
        assert "Nouveau résultat" in resolved_html
    finally:
        _cleanup(question_ids=[q])


# ---------------------------------------------------------------------------
# 4-5 : confidentialité — jamais de badge avant résultat visible
# ---------------------------------------------------------------------------

def test_open_bonus_question_never_shows_badge(client, participant):
    q = _seed_question("Question B8 ouverte (b8-test) ?", deadline=_FUTURE)
    _seed_score(q, participant["id"], 5, calculated_at=_PAST)  # score en avance
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        assert f'id="bonus-q-{q}"' in open_html
        assert "chip new" not in open_html
        assert 'data-bonus-next-action="new_result"' not in html
    finally:
        _cleanup(question_ids=[q])


def test_locked_unscored_bonus_question_never_shows_badge(client, participant):
    q = _seed_question("Question B8 en attente (b8-test) ?", deadline=_PAST)
    try:
        html = client.get(f"/p/{participant['token']}/bonus").text
        waiting_html = _section_html(html, "waiting")
        assert f'id="bonus-q-{q}"' in waiting_html
        assert "chip new" not in waiting_html
        assert 'data-bonus-next-action="new_result"' not in html
    finally:
        _cleanup(question_ids=[q])


# ---------------------------------------------------------------------------
# 6-8 : carte pré-tournoi — nouveau, ouvert, recalculé
# ---------------------------------------------------------------------------

def test_new_pt_result_shows_badge(client, participant):
    old_deadline = _set_pt_deadline(_PAST)
    try:
        _seed_pt_prediction(participant["id"])
        _seed_pt_score(participant["id"], "winner", 10, calculated_at=_PAST)
        html = client.get(f"/p/{participant['token']}/bonus").text
        resolved_html = _section_html(html, "resolved")
        card_html = _pt_card_html(resolved_html, "winner")
        assert 'id="bonus-pt-winner"' in card_html
        assert "chip new" in card_html
        assert "Nouveau résultat" in card_html
        assert f'href="#bonus-pt-winner"' in html
    finally:
        _cleanup(pt_scores_participant=participant["id"],
                  pt_prediction_participant=participant["id"])
        _set_pt_deadline(old_deadline)


def test_open_pt_never_shows_badge(client, participant):
    old_deadline = _set_pt_deadline(_FUTURE)
    try:
        _seed_pt_prediction(participant["id"])
        _seed_pt_score(participant["id"], "winner", 10, calculated_at=_PAST)  # avance
        html = client.get(f"/p/{participant['token']}/bonus").text
        open_html = _section_html(html, "open")
        card_html = _pt_card_html(open_html, "winner")
        assert "chip new" not in card_html
    finally:
        _cleanup(pt_scores_participant=participant["id"],
                  pt_prediction_participant=participant["id"])
        _set_pt_deadline(old_deadline)


def test_pt_badge_reappears_after_recalculation(client, participant):
    old_deadline = _set_pt_deadline(_PAST)
    try:
        _seed_pt_prediction(participant["id"])
        _seed_pt_score(participant["id"], "winner", 10, calculated_at=_PAST)
        client.get(f"/p/{participant['token']}/bonus")  # marque vu
        seen_html = client.get(f"/p/{participant['token']}/bonus").text
        assert "chip new" not in _pt_card_html(_section_html(seen_html, "resolved"), "winner")

        _update_pt_score_calculated_at(participant["id"], "winner", _PAST_RECALC)
        html = client.get(f"/p/{participant['token']}/bonus").text
        card_html = _pt_card_html(_section_html(html, "resolved"), "winner")
        assert "chip new" in card_html
    finally:
        _cleanup(pt_scores_participant=participant["id"],
                  pt_prediction_participant=participant["id"])
        _set_pt_deadline(old_deadline)


# ---------------------------------------------------------------------------
# 9 : vues propres par participant
# ---------------------------------------------------------------------------

def test_views_are_isolated_per_participant(client):
    q = _seed_question("Question B8 partagée (b8-test) ?", deadline=_PAST)
    a = _seed_participant("B8 Participant A")
    b = _seed_participant("B8 Participant B")
    _seed_score(q, a, 5, calculated_at=_PAST)
    _seed_score(q, b, 5, calculated_at=_PAST)
    try:
        token_a = _token_for(a)
        token_b = _token_for(b)

        html_a = client.get(f"/p/{token_a}/bonus").text
        assert "chip new" in _section_html(html_a, "resolved")
        views_a = _bonus_result_views(a)
        assert any(v["source_key"] == str(q) for v in views_a)

        views_b_before = _bonus_result_views(b)
        assert not any(v["source_key"] == str(q) for v in views_b_before)

        html_b = client.get(f"/p/{token_b}/bonus").text
        assert "chip new" in _section_html(html_b, "resolved")

        views_b_after = _bonus_result_views(b)
        assert any(v["source_key"] == str(q) for v in views_b_after)
    finally:
        _cleanup(question_ids=[q], participant_ids=[a, b])


def _token_for(participant_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT token FROM participants WHERE id=?", (participant_id,)
            )
            return (await row.fetchone())["token"]

    return run(_get())


# ---------------------------------------------------------------------------
# 10 : marquage seulement sur /bonus
# ---------------------------------------------------------------------------

def test_seen_marking_only_happens_on_bonus_page(client, participant):
    q = _seed_question("Question B8 home (b8-test) ?", deadline=_PAST)
    _seed_score(q, participant["id"], 5, calculated_at=_PAST)
    try:
        client.get(f"/p/{participant['token']}")
        views_after_home = _bonus_result_views(participant["id"])
        assert not any(v["source_key"] == str(q) for v in views_after_home)

        client.get(f"/p/{participant['token']}/bonus")
        views_after_bonus = _bonus_result_views(participant["id"])
        assert any(v["source_key"] == str(q) for v in views_after_bonus)
    finally:
        _cleanup(question_ids=[q])


# ---------------------------------------------------------------------------
# 13 : le badge est visible sur la première requête qui marque vu
# ---------------------------------------------------------------------------

def test_badge_visible_on_first_request_that_marks_seen(client, participant):
    q = _seed_question("Question B8 première visite (b8-test) ?", deadline=_PAST)
    _seed_score(q, participant["id"], 5, calculated_at=_PAST)
    try:
        views_before = _bonus_result_views(participant["id"])
        assert not any(v["source_key"] == str(q) for v in views_before)

        first_html = client.get(f"/p/{participant['token']}/bonus").text
        assert "Nouveau résultat" in _section_html(first_html, "resolved")

        views_after = _bonus_result_views(participant["id"])
        assert any(v["source_key"] == str(q) for v in views_after)

        second_html = client.get(f"/p/{participant['token']}/bonus").text
        assert "Nouveau résultat" not in _section_html(second_html, "resolved")
    finally:
        _cleanup(question_ids=[q])


# ---------------------------------------------------------------------------
# 11 : migration idempotente
# ---------------------------------------------------------------------------

def test_bonus_result_views_migration_is_idempotent(participant):
    async def _check():
        async with get_db() as db:
            columns = await db.execute("PRAGMA table_info(bonus_result_views)")
            names = {row["name"] for row in await columns.fetchall()}
            await db.execute(
                """INSERT INTO bonus_result_views
                   (participant_id, source, source_key, result_version)
                   VALUES (?, 'bonus_question', '999999', '2020-01-01T00:00:00')
                   ON CONFLICT(participant_id, source, source_key) DO NOTHING""",
                (participant["id"],),
            )
            await db.commit()
        await init_db()
        async with get_db() as db:
            row = await db.execute(
                """SELECT * FROM bonus_result_views
                   WHERE participant_id=? AND source='bonus_question' AND source_key='999999'""",
                (participant["id"],),
            )
            kept = await row.fetchone()
        return names, kept

    names, kept = run(_check())
    assert {"participant_id", "source", "source_key", "result_version", "seen_at"}.issubset(names)
    assert kept is not None

    async def _clean():
        async with get_db() as db:
            await db.execute(
                "DELETE FROM bonus_result_views WHERE source_key='999999'"
            )
            await db.commit()

    run(_clean())
