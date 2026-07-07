"""Événements de scoring bonus/pré-tournoi : le classement doit raisonner sur
TOUS les événements qui modifient les points, pas seulement les matchs.

Règle métier vérifiée ici (cf. plan de correction) :
- une correction bonus/pré-tournoi qui fait gagner/perdre des places doit être
  visible dans la flèche d'évolution (▲/▼), pas seulement dans le total ;
- une correction qui distribue des points sans changer de rang doit rester "="
  mais laisser une trace exploitable (le Reveal) ;
- une correction RÉTROACTIVE d'une question déjà corrigée doit être journalisée
  comme un DELTA (nouveau - ancien), jamais comme la nouvelle valeur absolue —
  sinon le sens du mouvement peut s'inverser (ex. 5 -> 2 pts doit valoir -3, pas
  +2) ;
- une re-sauvegarde qui ne change aucun point ne doit ajouter aucun événement
  (pas de mouvement fantôme, pas de doublon).
- des points bonus/pré-tournoi déjà attribués AVANT l'existence de cette table
  (legacy, en production) doivent être reconstruits par un backfill idempotent
  (`backfill_scoring_point_events`), sans quoi ces mouvements resteraient
  invisibles après déploiement.
"""
import contextlib
import uuid

from app.database import get_db
from app.scoring import (
    _log_scoring_point_events,
    _scoring_points_by_day,
    backfill_scoring_point_events,
    calculate_bonus_scores,
    get_rank_evolution,
    get_rankings,
    recalculate_pre_tournament_scores,
    sync_finalized_evolution_history,
)
from app.trophies import refresh_trophy_awards
from tests.conftest import run

_ISOLATED = (
    "sporting_day_rank_evolutions", "scoring_point_events", "trophy_awards",
    "scores", "predictions", "matches",
    "bonus_answers", "bonus_questions",
    "pre_tournament_scores", "pre_tournament_predictions",
)
_RESTORE_ORDER = (
    "matches", "predictions", "scores",
    "bonus_questions", "bonus_answers",
    "pre_tournament_predictions", "pre_tournament_scores",
    "scoring_point_events", "sporting_day_rank_evolutions", "trophy_awards",
)


@contextlib.contextmanager
def isolated_scoring_state():
    """Isole matchs/bonus/pré-tournoi/événements : la base de test est
    partagée (session), on repart d'une ardoise vierge pour ces tables le
    temps du scénario, puis on restaure l'état d'origine."""
    async def _backup():
        async with get_db() as db:
            await db.execute("PRAGMA foreign_keys=OFF")
            data = {}
            for table in _ISOLATED:
                rows = await db.execute(f"SELECT * FROM {table}")
                data[table] = [dict(r) for r in await rows.fetchall()]
                await db.execute(f"DELETE FROM {table}")
            await db.commit()
            return data

    async def _restore(data):
        async with get_db() as db:
            await db.execute("PRAGMA foreign_keys=OFF")
            for table in _ISOLATED:
                await db.execute(f"DELETE FROM {table}")
            for table in _RESTORE_ORDER:
                for row in data[table]:
                    cols = list(row.keys())
                    ph = ",".join("?" for _ in cols)
                    await db.execute(
                        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})",
                        [row[c] for c in cols],
                    )
            await db.commit()

    backup = run(_backup())
    try:
        yield
    finally:
        run(_restore(backup))


@contextlib.contextmanager
def isolated_pre_tournament_question(key):
    """Sauvegarde/restaure une ligne `pre_tournament_questions` (catalogue fixe,
    pas rejouable via `isolated_scoring_state`) le temps de manipuler
    `correct_answer`/`points_value` pour un scénario."""
    async def _backup():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM pre_tournament_questions WHERE key=?", (key,)
            )
            return dict(await row.fetchone())

    async def _restore(data):
        async with get_db() as db:
            cols = [c for c in data if c != "key"]
            assignments = ",".join(f"{c}=?" for c in cols)
            await db.execute(
                f"UPDATE pre_tournament_questions SET {assignments} WHERE key=?",
                [data[c] for c in cols] + [key],
            )
            await db.commit()

    backup = run(_backup())
    try:
        yield
    finally:
        run(_restore(backup))


def _make_participant(name):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                (name, f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _make_match(number, match_date, kickoff="18:00", result="team1", s1=1, s2=0):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight, result, score_team1, score_team2)
                   VALUES (?, 'group', ?, ?, 'France', 'Brésil', 1, ?, ?, ?)""",
                (number, match_date, kickoff, result, s1, s2),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _award_match(pid, mid, points):
    async def _create():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, mid, points),
            )
            await db.commit()

    run(_create())


def _make_bonus_question(points_value=5, answer_type="choice", scoring_mode="exact",
                          options=None, deadline="2020-01-01T00:00:00"):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value,
                    scoring_mode, is_published, deadline)
                   VALUES (?, 'group', ?, ?, ?, ?, 1, ?)""",
                ("Question bonus de test", answer_type, options, points_value,
                 scoring_mode, deadline),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _answer_bonus(question_id, pid, answer):
    async def _create():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO bonus_answers (participant_id, question_id, answer) VALUES (?,?,?)",
                (pid, question_id, answer),
            )
            await db.commit()

    run(_create())


def _set_bonus_correct_answer(question_id, correct_answer):
    async def _c():
        async with get_db() as db:
            await db.execute(
                "UPDATE bonus_questions SET correct_answer=? WHERE id=?",
                (correct_answer, question_id),
            )
            await db.commit()

    run(_c())


def _calculate_bonus(question_id):
    run(calculate_bonus_scores(question_id))


def _events(source, source_key):
    async def _q():
        async with get_db() as db:
            rows = await db.execute(
                """SELECT participant_id, delta, occurred_at FROM scoring_point_events
                   WHERE source=? AND source_key=? ORDER BY id""",
                (source, source_key),
            )
            return [dict(r) for r in await rows.fetchall()]

    return run(_q())


def _backdate_events(source, source_key, occurred_at):
    async def _c():
        async with get_db() as db:
            await db.execute(
                "UPDATE scoring_point_events SET occurred_at=? WHERE source=? AND source_key=?",
                (occurred_at, source, source_key),
            )
            await db.commit()

    run(_c())


def _evolution():
    async def _q():
        async with get_db() as db:
            return await get_rank_evolution(db)

    return run(_q())


def _rankings():
    async def _q():
        async with get_db() as db:
            return await get_rankings(db, "general")

    return run(_q())


def _trophy_awards(pid):
    async def _q():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT trophy_key, detail FROM trophy_awards WHERE participant_id=?",
                (pid,),
            )
            return {(r["trophy_key"], r["detail"]) for r in await rows.fetchall()}

    return run(_q())


def _seed_legacy_score(table, columns, values):
    """Insère une ligne `scores`/`pre_tournament_scores` directement (sans
    passer par calculate_bonus_scores/recalculate_pre_tournament_scores), pour
    simuler des points bonus/pré-tournoi attribués AVANT que le backfill/la
    journalisation des événements n'existent (cas réel de production)."""
    async def _c():
        async with get_db() as db:
            placeholders = ",".join("?" for _ in columns)
            await db.execute(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                values,
            )
            await db.commit()

    run(_c())


def _run_backfill():
    async def _c():
        async with get_db() as db:
            await backfill_scoring_point_events(db)
            await db.commit()

    run(_c())


def _resync_and_refresh_trophies():
    async def _c():
        async with get_db() as db:
            await sync_finalized_evolution_history(db)
            await refresh_trophy_awards(db)
            await db.commit()

    run(_c())


def _submit_pre_tournament(pid, **fields):
    cols = list(fields)
    async def _c():
        async with get_db() as db:
            placeholders = ",".join("?" for _ in cols)
            updates = ",".join(f"{c}=excluded.{c}" for c in cols)
            await db.execute(
                f"""INSERT INTO pre_tournament_predictions (participant_id, {",".join(cols)})
                    VALUES (?, {placeholders})
                    ON CONFLICT(participant_id) DO UPDATE SET {updates}""",
                (pid, *[fields[c] for c in cols]),
            )
            await db.commit()

    run(_c())


def _set_pre_tournament_question(key, correct_answer, points_value=None):
    async def _c():
        async with get_db() as db:
            if points_value is not None:
                await db.execute(
                    "UPDATE pre_tournament_questions SET correct_answer=?, points_value=? WHERE key=?",
                    (correct_answer, points_value, key),
                )
            else:
                await db.execute(
                    "UPDATE pre_tournament_questions SET correct_answer=? WHERE key=?",
                    (correct_answer, key),
                )
            await db.commit()

    run(_c())


def test_bonus_correction_moves_rank_several_places(client):
    """Un bonus qui distribue assez de points fait gagner plusieurs places :
    le mouvement doit être visible dans la flèche d'évolution."""
    with isolated_scoring_state():
        leader = _make_participant("BonusLeader")
        climber = _make_participant("BonusClimber")
        m = _make_match(980001, "2000-01-01")
        _award_match(leader, m, 20)
        _award_match(climber, m, 2)

        q = _make_bonus_question(points_value=30, answer_type="choice",
                                  options='["A","B"]')
        _answer_bonus(q, leader, "B")
        _answer_bonus(q, climber, "A")
        _set_bonus_correct_answer(q, "A")
        _calculate_bonus(q)
        # climber : 2 + 30 = 32 > leader : 20 + 0 = 20 → climber passe 1er.

        evo = _evolution()
        assert evo["deltas"][climber] == 1
        assert evo["deltas"][leader] == -1


def test_bonus_correction_no_rank_change_but_points_registered(client):
    """Un bonus qui distribue des points identiques aux deux participants ne
    change pas le rang (=), mais l'événement doit exister (visible ensuite
    dans le Reveal, cf. `_scoring_points_by_day`)."""
    with isolated_scoring_state():
        a = _make_participant("EvenA")
        b = _make_participant("EvenB")
        m = _make_match(980002, "2000-01-01")
        _award_match(a, m, 10)
        _award_match(b, m, 4)

        q = _make_bonus_question(points_value=6, answer_type="choice",
                                  options='["A","B"]')
        _answer_bonus(q, a, "A")
        _answer_bonus(q, b, "A")
        _set_bonus_correct_answer(q, "A")
        _calculate_bonus(q)
        # a: 10+6=16, b: 4+6=10 → ordre inchangé (a toujours devant b).

        evo = _evolution()
        assert evo["deltas"][a] == 0
        assert evo["deltas"][b] == 0

        events = _events("bonus", str(q))
        by_pid = {e["participant_id"]: e["delta"] for e in events}
        assert by_pid[a] == 6
        assert by_pid[b] == 6

        by_day = run(_scoring_points_by_day_call())
        assert any(pts.get(a) == 6 and pts.get(b) == 6 for pts in by_day.values())


async def _scoring_points_by_day_call():
    async with get_db() as db:
        return await _scoring_points_by_day(db)


def test_pretournament_correction_moves_rank(client):
    """Une correction pré-tournoi provoque un mouvement de classement visible."""
    with isolated_scoring_state(), isolated_pre_tournament_question("top_scorer"):
        a = _make_participant("PreTA")
        b = _make_participant("PreTB")
        m = _make_match(980003, "2000-01-01")
        _award_match(a, m, 5)
        _award_match(b, m, 4)

        _submit_pre_tournament(a, top_scorer="Mbappé")
        _submit_pre_tournament(b, top_scorer="Haaland")
        _set_pre_tournament_question("top_scorer", "Haaland", points_value=10)
        run(recalculate_pre_tournament_scores())
        # a: 5+0=5, b: 4+10=14 → b passe devant a.

        evo = _evolution()
        assert evo["deltas"][b] == 1
        assert evo["deltas"][a] == -1


def test_bonus_resave_same_answer_no_duplicate_event(client):
    """Re-corriger une question bonus avec la même réponse ne doit ajouter
    aucun nouvel événement (pas de mouvement fantôme, pas de doublon)."""
    with isolated_scoring_state():
        a = _make_participant("ResaveA")
        b = _make_participant("ResaveB")
        q = _make_bonus_question(points_value=5, answer_type="choice",
                                  options='["A","B"]')
        _answer_bonus(q, a, "A")
        _answer_bonus(q, b, "B")
        _set_bonus_correct_answer(q, "A")
        _calculate_bonus(q)
        first_events = _events("bonus", str(q))
        assert len(first_events) == 1  # seul a a gagné des points (delta != 0)

        # Re-sauvegarde : même réponse correcte, recalcul redéclenché (comme un
        # simple edit du formulaire admin qui ne change rien au fond).
        _calculate_bonus(q)
        second_events = _events("bonus", str(q))
        assert second_events == first_events


def test_retroactive_bonus_correction_logs_delta_not_absolute_value(client):
    """_log_scoring_point_events doit journaliser le DELTA réel (nouveau -
    ancien), jamais la valeur absolue courante — sinon une correction
    rétroactive (5 -> 2 pts) afficherait +2 au lieu de -3."""
    with isolated_scoring_state():
        a = _make_participant("RetroLedgerA")

        async def _run():
            async with get_db() as db:
                await _log_scoring_point_events(
                    db, "bonus", "999", old_points={a: 5}, new_points={a: 2}
                )
                await db.commit()

        run(_run())
        events = _events("bonus", "999")
        assert len(events) == 1
        assert events[0]["participant_id"] == a
        assert events[0]["delta"] == -3  # PAS 2 (la valeur absolue courante)


def test_retroactive_pretournament_correction_flips_rank_arrow_correctly(client):
    """Scénario bout-en-bout du risque identifié en revue : une question déjà
    corrigée est re-corrigée avec une réponse différente, qui fait perdre des
    points à un participant déjà en tête. La flèche du jour doit refléter la
    VRAIE perte (delta négatif) et donc pointer ▼, jamais ▲."""
    with isolated_scoring_state(), isolated_pre_tournament_question("total_goals"):
        a = _make_participant("RetroA")   # a la meilleure prédiction, puis perd des points
        b = _make_participant("RetroB")   # stable, finit par dépasser a
        c = _make_participant("RetroC")   # ancre neutre, ne bouge jamais

        m = _make_match(980010, "2000-01-01")
        _award_match(c, m, 5)  # C : 5 pts fixes, journée antérieure

        _submit_pre_tournament(a, total_goals=10)
        _submit_pre_tournament(b, total_goals=4)

        # Première correction ("hier") : bonne réponse = 10 → a exact (10 pts),
        # b loin (0 pt). a est alors 1er (10 > 5 > 0).
        _set_pre_tournament_question("total_goals", "10", points_value=10)
        run(recalculate_pre_tournament_scores())
        _backdate_events("pre_tournament", "total_goals", "2000-01-02T20:00:00")

        # Deuxième correction ("aujourd'hui") : l'admin corrige une erreur de
        # saisie, la vraie réponse était 13 → a se retrouve "proche" (10 pts -> 4
        # pts, écart 3 = marge de tolérance), b reste à 0 (écart 9, hors marge).
        # a passe de 1er (10) à 2e (4 < 5 de C) : un VRAI recul.
        _set_pre_tournament_question("total_goals", "13", points_value=10)
        run(recalculate_pre_tournament_scores())

        rankings = {r["id"]: r for r in _rankings()}
        assert rankings[a]["total_points"] == 4
        assert rankings[c]["total_points"] == 5
        assert rankings[b]["total_points"] == 0

        today_events = [
            e for e in _events("pre_tournament", "total_goals")
            if e["occurred_at"] > "2000-01-02T20:00:00"
        ]
        by_pid = {e["participant_id"]: e["delta"] for e in today_events}
        assert by_pid[a] == -6  # 4 - 10, PAS la valeur absolue 4

        evo = _evolution()
        # a recule (10 -> 4 pts) et passe derrière c : delta doit être négatif.
        assert evo["deltas"][a] < 0
        assert evo["day"] > "2000-01-02"


def test_general_ranking_total_matches_scores_and_pretournament_sum(client):
    """Le classement général reste la somme exacte matchs + bonus + pré-tournoi,
    quel que soit le nouveau mécanisme de journalisation des événements."""
    with isolated_scoring_state(), isolated_pre_tournament_question("top_scorer"):
        a = _make_participant("SumA")
        m = _make_match(980020, "2000-01-01")
        _award_match(a, m, 7)

        q = _make_bonus_question(points_value=4, answer_type="choice", options='["A","B"]')
        _answer_bonus(q, a, "A")
        _set_bonus_correct_answer(q, "A")
        _calculate_bonus(q)

        _submit_pre_tournament(a, top_scorer="Mbappé")
        _set_pre_tournament_question("top_scorer", "Mbappé", points_value=9)
        run(recalculate_pre_tournament_scores())

        total = next(r["total_points"] for r in _rankings() if r["id"] == a)
        assert total == 7 + 4 + 9


def _seed_scoring_event(source, source_key, pid, delta, occurred_at):
    async def _c():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO scoring_point_events (source, source_key, participant_id, delta, occurred_at)
                   VALUES (?,?,?,?,?)""",
                (source, source_key, pid, delta, occurred_at),
            )
            await db.commit()

    run(_c())


def test_backfill_reconstructs_preexisting_bonus_points_and_is_idempotent(client):
    """Cas réel de production : `scores` contient déjà des points bonus
    attribués AVANT l'existence de `scoring_point_events` (legacy, aucun
    événement journalisé), sur une question partagée par PLUSIEURS
    participants. Le backfill doit reconstruire un événement par participant
    concerné (delta=points, occurred_at=calculated_at), ne jamais créer de
    doublon en cas de rejeu, et rendre le mouvement de classement + le
    trophée "Le Grimpeur" visibles une fois `sync_finalized_evolution_history`/
    `refresh_trophy_awards` relancés.

    Couvre aussi explicitement le risque identifié en revue : un participant
    (`middle`) a DÉJÀ un événement journalisé pour cette question (simule un
    recalcul en direct survenu après déploiement, ou un backfill partiel
    antérieur) — ça ne doit PAS empêcher le backfill d'un AUTRE participant
    (`climber`) resté legacy sur la MÊME question. Le garde-fou d'idempotence
    doit être scopé par (source, clé, participant), pas seulement (source, clé)."""
    with isolated_scoring_state():
        leader = _make_participant("BackfillLeader")
        middle = _make_participant("BackfillMiddle")
        climber = _make_participant("BackfillClimber")
        m = _make_match(980040, "2000-01-01")
        _award_match(leader, m, 20)
        _award_match(middle, m, 5)
        _award_match(climber, m, 0)

        q = _make_bonus_question(points_value=25, answer_type="choice", options='["A","B"]')
        _answer_bonus(q, climber, "A")
        _answer_bonus(q, middle, "A")
        _answer_bonus(q, leader, "B")
        _set_bonus_correct_answer(q, "A")
        legacy_occurred_at = "2024-06-01T10:00:00"
        # Trois lignes `scores` legacy sur LA MÊME question : climber (25),
        # middle (12), leader (0, filtré car nul).
        _seed_legacy_score(
            "scores", ["participant_id", "bonus_question_id", "points", "calculated_at"],
            (climber, q, 25, legacy_occurred_at),
        )
        _seed_legacy_score(
            "scores", ["participant_id", "bonus_question_id", "points", "calculated_at"],
            (middle, q, 12, legacy_occurred_at),
        )
        _seed_legacy_score(
            "scores", ["participant_id", "bonus_question_id", "points", "calculated_at"],
            (leader, q, 0, legacy_occurred_at),
        )

        # middle a DÉJÀ un événement pour cette question (recalcul en direct
        # ou backfill partiel antérieur) — climber, lui, est encore vierge.
        _seed_scoring_event("bonus", str(q), middle, 12, legacy_occurred_at)
        assert {e["participant_id"] for e in _events("bonus", str(q))} == {middle}

        _run_backfill()
        events_by_pid = {e["participant_id"]: e for e in _events("bonus", str(q))}
        # climber doit être backfillé MALGRÉ l'événement préexistant de middle.
        assert climber in events_by_pid
        assert events_by_pid[climber]["delta"] == 25
        assert events_by_pid[climber]["occurred_at"] == legacy_occurred_at
        # middle : toujours un seul événement (pas de doublon créé pour lui).
        assert sum(1 for e in _events("bonus", str(q)) if e["participant_id"] == middle) == 1
        # leader : 0 pt, jamais journalisé (rien à journaliser pour lui).
        assert leader not in events_by_pid

        # Rejoué : idempotent pour tout le monde, aucun doublon.
        before = _events("bonus", str(q))
        _run_backfill()
        assert _events("bonus", str(q)) == before

        _resync_and_refresh_trophies()

        evo = _evolution()
        # leader 20, middle 5+12=17, climber 0+25=25 (courant) ;
        # avant le jour du bonus : leader 20, middle 5, climber 0.
        # -> climber 3e->1er (+2), leader 1er->2e (-1), middle 2e->3e (-1).
        assert evo["deltas"][climber] == 2
        assert evo["deltas"][leader] == -1
        assert evo["deltas"][middle] == -1
        assert ("grimpeur", "2024-06-01") in _trophy_awards(climber)
        assert ("grimpeur", "2024-06-01") not in _trophy_awards(middle)


def test_backfill_reconstructs_preexisting_pretournament_points(client):
    """Équivalent pré-tournoi, avec la même question partagée par plusieurs
    participants et un événement préexistant pour l'un d'eux : le backfill
    doit reconstruire l'autre participant sans toucher à l'existant, et
    rendre le mouvement de classement visible après resynchronisation."""
    with isolated_scoring_state(), isolated_pre_tournament_question("top_scorer"):
        a = _make_participant("BackfillPtA")
        b = _make_participant("BackfillPtB")
        c = _make_participant("BackfillPtC")
        m = _make_match(980041, "2000-01-01")
        _award_match(a, m, 5)
        _award_match(b, m, 0)
        _award_match(c, m, 2)

        _set_pre_tournament_question("top_scorer", "Haaland", points_value=10)
        legacy_occurred_at = "2024-06-02T09:00:00"
        _seed_legacy_score(
            "pre_tournament_scores", ["participant_id", "question_key", "points", "calculated_at"],
            (b, "top_scorer", 10, legacy_occurred_at),
        )
        _seed_legacy_score(
            "pre_tournament_scores", ["participant_id", "question_key", "points", "calculated_at"],
            (c, "top_scorer", 10, legacy_occurred_at),
        )
        _seed_legacy_score(
            "pre_tournament_scores", ["participant_id", "question_key", "points", "calculated_at"],
            (a, "top_scorer", 0, legacy_occurred_at),
        )

        # c a DÉJÀ un événement pour cette question ; b est encore legacy.
        _seed_scoring_event("pre_tournament", "top_scorer", c, 10, legacy_occurred_at)
        assert {e["participant_id"] for e in _events("pre_tournament", "top_scorer")} == {c}

        _run_backfill()
        events_by_pid = {e["participant_id"]: e for e in _events("pre_tournament", "top_scorer")}
        # b doit être backfillé MALGRÉ l'événement préexistant de c sur la même question.
        assert b in events_by_pid
        assert events_by_pid[b]["delta"] == 10
        assert events_by_pid[b]["occurred_at"] == legacy_occurred_at
        assert sum(1 for e in _events("pre_tournament", "top_scorer") if e["participant_id"] == c) == 1
        assert a not in events_by_pid  # 0 pt, jamais journalisé

        # Rejoué : idempotent, aucun doublon pour personne.
        before = _events("pre_tournament", "top_scorer")
        _run_backfill()
        assert _events("pre_tournament", "top_scorer") == before

        _resync_and_refresh_trophies()

        evo = _evolution()
        # b : 0+10=10, c : 2+10=12, a : 5+0=5 → b et c passent devant a.
        assert evo["deltas"][b] > 0
        assert evo["deltas"][c] > 0
        assert evo["deltas"][a] < 0
