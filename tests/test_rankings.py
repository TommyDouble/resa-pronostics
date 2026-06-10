import uuid

from app.database import get_db
from app.scoring import get_rankings
from tests.conftest import run


def test_rankings_use_exact_scores_then_outcomes_then_name(client):
    async def _seed():
        async with get_db() as db:
            participants = {}
            for name in ("Tie Exact", "Tie Outcome", "Alpha Bonus", "Zulu Bonus"):
                token = str(uuid.uuid4())
                cursor = await db.execute(
                    """INSERT INTO participants (name, email, token, is_confirmed)
                       VALUES (?, ?, ?, 1)""",
                    (name, f"{token}@test.local", token),
                )
                participants[name] = cursor.lastrowid

            base_match = 910000 + participants["Tie Exact"] * 10
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, group_name, match_date, kickoff_time,
                    team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?, 'group', 'A', '2026-06-20', '18:00',
                           'France', 'Brésil', 1, 2, 1, 'team1')""",
                (base_match,),
            )
            exact_match_id = cursor.lastrowid
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, group_name, match_date, kickoff_time,
                    team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?, 'group', 'A', '2026-06-21', '18:00',
                           'Argentine', 'Japon', 2, 2, 1, 'team1')""",
                (base_match + 1,),
            )
            outcome_match_id = cursor.lastrowid

            await db.execute(
                """INSERT INTO predictions
                   (participant_id, match_id, prediction, exact_score_team1, exact_score_team2)
                   VALUES (?, ?, 'team1', 2, 1)""",
                (participants["Tie Exact"], exact_match_id),
            )
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?, ?, 4)",
                (participants["Tie Exact"], exact_match_id),
            )
            await db.execute(
                """INSERT INTO predictions
                   (participant_id, match_id, prediction, exact_score_team1, exact_score_team2)
                   VALUES (?, ?, 'team1', 3, 0)""",
                (participants["Tie Outcome"], outcome_match_id),
            )
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?, ?, 4)",
                (participants["Tie Outcome"], outcome_match_id),
            )
            for name in ("Alpha Bonus", "Zulu Bonus"):
                await db.execute(
                    """INSERT INTO pre_tournament_scores (participant_id, question_key, points)
                       VALUES (?, 'revelation', 4)""",
                    (participants[name],),
                )
            await db.commit()
            return participants

    participant_ids = run(_seed())
    rankings = run(get_rankings())
    filtered = [r for r in rankings if r["id"] in participant_ids.values()]

    assert [r["id"] for r in filtered] == [
        participant_ids["Tie Exact"],
        participant_ids["Tie Outcome"],
        participant_ids["Alpha Bonus"],
        participant_ids["Zulu Bonus"],
    ]
    assert filtered[0]["exact_count"] == 1
    assert filtered[0]["correct_outcome_count"] == 1
    assert filtered[1]["exact_count"] == 0
    assert filtered[1]["correct_outcome_count"] == 1
