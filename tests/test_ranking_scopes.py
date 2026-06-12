"""Classements par périmètre, remontada, départements, évolution."""
import uuid

from app.database import get_db
from app.scoring import (
    get_department_rankings,
    get_rank_evolution,
    get_rankings,
    get_remontada,
)
from tests.conftest import run


def make_participant(name, department=None):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, email, token, is_confirmed, department)
                   VALUES (?,?,?,1,?)""",
                (name, f"{token}@test.local", token, department),
            )
            await db.commit()
            return cursor.lastrowid

    return {"id": run(_create()), "token": token}


def make_match(phase, number):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?,?, '2000-01-01', '12:00', 'A', 'B', ?, 1, 0, 'team1')""",
                (number, phase, 2 if phase != "group" else 1),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def add_score(participant_id, match_id, points):
    async def _add():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (participant_id, match_id, points),
            )
            await db.commit()

    run(_add())


def test_scoped_rankings_and_remontada_and_departments(client):
    alice = make_participant("Alice Scope", "Finances")
    bob = make_participant("Bob Scope", "Expérience Client")
    group_match = make_match("group", 800001)
    ko_match = make_match("quarter", 800002)
    # Alice forte en groupes, Bob fort en phase finale.
    add_score(alice["id"], group_match, 4)
    add_score(bob["id"], group_match, 0)
    add_score(alice["id"], ko_match, 0)
    add_score(bob["id"], ko_match, 6)

    async def _all():
        async with get_db() as db:
            return (
                await get_rankings(db, scope="groups"),
                await get_rankings(db, scope="knockout"),
                await get_remontada(db),
                await get_department_rankings(db),
            )

    groups, knockout, remontada, departments = run(_all())
    g = {r["id"]: r["total_points"] for r in groups}
    k = {r["id"]: r["total_points"] for r in knockout}
    assert g[alice["id"]] == 4 and g[bob["id"]] == 0
    assert k[alice["id"]] == 0 and k[bob["id"]] == 6

    # Remontada: Bob progresse grâce à la phase finale.
    rem = {r["id"]: r for r in remontada}
    assert rem[bob["id"]]["delta"] > 0
    assert rem[bob["id"]]["remontada_rank"] == 1

    dep = {d["department"]: d for d in departments}
    assert dep["Finances"]["members"] >= 1
    assert dep["Finances"]["average"] >= 0


def test_rank_evolution_uses_previous_day_snapshot(client):
    carol = make_participant("Carol Evo")

    async def _seed_snapshots():
        async with get_db() as db:
            # Snapshot d'hier: Carol était 5e.
            await db.execute(
                """INSERT INTO ranking_snapshots (snapshot_date, participant_id, rank, total_points)
                   VALUES ('2000-01-01', ?, 5, 0)""",
                (carol["id"],),
            )
            await db.commit()
            return await get_rank_evolution(db)

    evolution = run(_seed_snapshots())
    assert carol["id"] in evolution["deltas"]
    # Carol est forcément mieux ou pareil aujourd'hui qu'un rang 5 fictif très bas…
    assert isinstance(evolution["deltas"][carol["id"]], int)
    assert evolution["since"] is not None
