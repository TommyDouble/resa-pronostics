"""Flèches d'évolution : référence = dernier mouvement réel du classement."""
import uuid
from datetime import date, timedelta

from app.database import get_db
from app.scoring import _local_today, get_rank_evolution
from tests.conftest import run

# Points distinctifs, assertions relatives uniquement (DB de test partagée).
PTS_A, PTS_B = 5151, 5150


def _make_participant(name, points, match_id):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                (name, f"{token}@test.local", token),
            )
            pid = cursor.lastrowid
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, match_id, points),
            )
            await db.commit()
            return pid

    return run(_create())


def _make_match(number):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'group', '2000-01-01', '12:00', 'France', 'Brésil', 1)""",
                (number,),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _snapshot(day_offset, rank_by_pid):
    day = (date.fromisoformat(_local_today()) + timedelta(days=day_offset)).isoformat()

    async def _create():
        async with get_db() as db:
            for pid, rank in rank_by_pid.items():
                await db.execute(
                    """INSERT INTO ranking_snapshots (snapshot_date, participant_id, rank, total_points)
                       VALUES (?,?,?,0)
                       ON CONFLICT(snapshot_date, participant_id)
                       DO UPDATE SET rank=excluded.rank""",
                    (day, pid, rank),
                )
            await db.commit()

    run(_create())


def _evolution():
    async def _q():
        async with get_db() as db:
            return await get_rank_evolution(db)

    return run(_q())


def test_yesterday_evolution_survives_midnight_until_first_result(client):
    # La sélection de la référence se fait sur les dates de toute la table :
    # on isole le scénario en mettant de côté les snapshots des autres tests.
    async def _backup_and_clear():
        async with get_db() as db:
            rows = await db.execute("SELECT * FROM ranking_snapshots")
            backup = [dict(r) for r in await rows.fetchall()]
            await db.execute("DELETE FROM ranking_snapshots")
            await db.commit()
            return backup

    async def _restore(backup):
        async with get_db() as db:
            await db.execute("DELETE FROM ranking_snapshots")
            for r in backup:
                await db.execute(
                    """INSERT INTO ranking_snapshots (snapshot_date, participant_id, rank, total_points)
                       VALUES (?,?,?,?)""",
                    (r["snapshot_date"], r["participant_id"], r["rank"], r["total_points"]),
                )
            await db.commit()

    backup = run(_backup_and_clear())
    try:
        mid = _make_match(960001)
        # Classement actuel : A devant B.
        a = _make_participant("Evo Alpha", PTS_A, mid)
        b = _make_participant("Evo Beta", PTS_B, mid)

        # Avant-hier B était devant A ; hier soir (dernier mouvement) A est
        # passé devant. Aucun encodage encore aujourd'hui.
        _snapshot(-2, {a: 2, b: 1})
        _snapshot(-1, {a: 1, b: 2})

        evolution = _evolution()
        # Après minuit, l'évolution d'hier reste visible : A a gagné une place.
        assert evolution[a] == 1
        assert evolution[b] == -1

        # Premier encodage du jour : un snapshot daté d'aujourd'hui apparaît.
        # La référence redevient « hier soir » → mouvements du jour (aucun ici).
        _snapshot(0, {a: 1, b: 2})
        evolution = _evolution()
        assert evolution[a] == 0
        assert evolution[b] == 0
    finally:
        run(_restore(backup))
