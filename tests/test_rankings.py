import uuid

from app.database import get_db
from app.scoring import get_rankings
from tests.conftest import run


def test_rankings_use_total_points_only_with_shared_ranks(client):
    async def _seed():
        async with get_db() as db:
            participants = {}
            for name, points in (
                ("Alice Total", 10000),
                ("Bob Total", 10000),
                ("Carla Total", 9990),
                ("Dario Total", 9980),
            ):
                token = str(uuid.uuid4())
                cursor = await db.execute(
                    """INSERT INTO participants (name, email, token, is_confirmed)
                       VALUES (?, ?, ?, 1)""",
                    (name, f"{token}@test.local", token),
                )
                participants[name] = cursor.lastrowid
                await db.execute(
                    """INSERT INTO pre_tournament_scores
                       (participant_id, question_key, points)
                       VALUES (?, 'revelation', ?)""",
                    (cursor.lastrowid, points),
                )
            await db.commit()
            return participants

    participant_ids = run(_seed())
    rankings = run(get_rankings())
    filtered = [r for r in rankings if r["id"] in participant_ids.values()]

    assert [(r["name"], r["total_points"], r["rank"]) for r in filtered] == [
        ("Alice Total", 10000, 1),
        ("Bob Total", 10000, 1),
        ("Carla Total", 9990, 3),
        ("Dario Total", 9980, 4),
    ]
