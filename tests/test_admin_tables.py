"""Admin tables expose client-side filtering and sorting hooks."""
from app.database import get_db
from tests.conftest import run


def _seed_conditional_admin_tables(participant_id):
    async def _seed():
        async with get_db() as db:
            match_row = await db.execute("SELECT id FROM matches ORDER BY id LIMIT 1")
            match = await match_row.fetchone()
            if match:
                await db.execute(
                    """UPDATE matches
                       SET score_team1=1, score_team2=0, result='team1', created_at=datetime('now')
                       WHERE id=?""",
                    (match["id"],),
                )
            else:
                number_row = await db.execute(
                    "SELECT COALESCE(MAX(match_number), 0) + 1000 AS number FROM matches"
                )
                match_number = (await number_row.fetchone())["number"]
                await db.execute(
                    """INSERT INTO matches
                       (match_number, phase, group_name, match_date, kickoff_time,
                        team1_name, team2_name, score_team1, score_team2, result, created_at)
                       VALUES (?, 'group', 'A', '2030-01-01', '12:00',
                               'France', 'Belgique', 1, 0, 'team1', datetime('now'))""",
                    (match_number,),
                )
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value, correct_answer, deadline)
                   VALUES (?, 'round_of_32', 'choice', ?, 5, 'Oui', '2030-01-01T12:00:00')""",
                ("Question triable ?", '["Oui","Non"]'),
            )
            await db.execute(
                """INSERT OR IGNORE INTO bonus_answers (participant_id, question_id, answer)
                   VALUES (?, ?, 'Oui')""",
                (participant_id, cursor.lastrowid),
            )
            await db.commit()

    run(_seed())


def test_main_admin_tables_have_sorting_and_filtering_hooks(admin_client, participant):
    _seed_conditional_admin_tables(participant["id"])

    endpoints = [
        "/admin/participants",
        "/admin/pronostics?view=matches",
        "/admin/pronostics?view=pre_tournament",
        "/admin/pronostics?view=bonus",
        "/admin/matches",
        "/admin/bonus",
        "/admin/dashboard",
    ]

    for endpoint in endpoints:
        response = admin_client.get(endpoint)
        assert response.status_code == 200
        assert "data-admin-table" in response.text
        assert "data-sort=" in response.text

    participants_page = admin_client.get("/admin/participants")
    assert 'data-admin-search="participant-search"' in participants_page.text
