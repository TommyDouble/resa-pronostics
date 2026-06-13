"""Flèches d'évolution (piste B) : déterministe, basée sur la journée de kickoff.

Aucun snapshot : la référence est recalculée à chaque affichage à partir des
dates de coup d'envoi (fuseau d'affichage), comme la page Pronos. La flèche
montre le mouvement provoqué par les matchs de la dernière journée jouée.

get_rank_evolution est global (tous les matchs de la base). La base de test
étant partagée, on isole chaque scénario en mettant de côté matchs / scores /
pronostics le temps du test, puis on les restaure.
"""
import contextlib
import uuid

from app.database import get_db
from app.scoring import get_rank_evolution
from tests.conftest import run

# Tout ce qui alimente le classement général : on repart d'une base vierge
# le temps du test pour que les flèches ne dépendent que de nos fixtures.
_ISOLATED = ("scores", "predictions", "matches", "pre_tournament_scores")
_RESTORE_ORDER = ("matches", "predictions", "scores", "pre_tournament_scores")


@contextlib.contextmanager
def isolated_match_state():
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


def _make_match(number, match_date, kickoff="18:00"):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight, result, score_team1, score_team2)
                   VALUES (?, 'group', ?, ?, 'France', 'Brésil', 1, 'team1', 1, 0)""",
                (number, match_date, kickoff),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _award(pid, mid, points):
    async def _create():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, mid, points),
            )
            await db.commit()

    run(_create())


def _evolution():
    async def _q():
        async with get_db() as db:
            return await get_rank_evolution(db)

    return run(_q())


def test_evolution_reflects_last_match_day_only(client):
    with isolated_match_state():
        a = _make_participant("Piste Alpha")
        b = _make_participant("Piste Beta")
        m_day1 = _make_match(970001, "2035-01-01")
        m_day2 = _make_match(970002, "2035-01-02")

        # Jour 1 : B mène (10 vs 2) → baseline avant jour 2 : B 1er, A 2e.
        _award(a, m_day1, 2)
        _award(b, m_day1, 10)
        # Jour 2 : A explose (12), B rien → total A 14, B 10 → A passe 1er.
        _award(a, m_day2, 12)
        _award(b, m_day2, 0)

        evo = _evolution()
        assert evo["day"] == "2035-01-02"
        assert evo["deltas"][a] == 1
        assert evo["deltas"][b] == -1


def test_evolution_empty_on_first_match_day(client):
    with isolated_match_state():
        a = _make_participant("Solo Alpha")
        b = _make_participant("Solo Beta")
        m = _make_match(970003, "2035-02-01")
        _award(a, m, 4)
        _award(b, m, 0)

        evo = _evolution()
        # Une seule journée de résultats → pas de référence, pas de flèche.
        assert evo == {"deltas": {}, "day": None}


def test_evolution_independent_of_encoding_order(client):
    """Ne dépend que des dates de kickoff, pas de l'ordre d'insertion."""
    with isolated_match_state():
        a = _make_participant("Ordre Alpha")
        b = _make_participant("Ordre Beta")
        # Insertion du jour 2 AVANT le jour 1 : sans effet sur le résultat.
        m_day2 = _make_match(970005, "2035-03-02")
        m_day1 = _make_match(970004, "2035-03-01")
        _award(a, m_day1, 5)
        _award(b, m_day1, 5)   # ex æquo après jour 1
        _award(a, m_day2, 6)   # A creuse l'écart le jour 2
        _award(b, m_day2, 0)

        evo = _evolution()
        assert evo["day"] == "2035-03-02"
        # A était ex æquo avec B (rang 1) puis passe seul 1er : A monte, B stagne/descend.
        assert evo["deltas"][a] >= 0
        assert evo["deltas"][b] <= 0
        assert evo["deltas"][a] - evo["deltas"][b] >= 1
