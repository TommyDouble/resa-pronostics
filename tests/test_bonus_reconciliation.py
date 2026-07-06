from app.database import get_db
from app.scoring import get_rankings
from tests.conftest import run


def _seed_bonus_and_pretournament(participant_id, *, bonus_points, pretournament_points):
    async def _seed():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value,
                    correct_answer, scoring_mode, is_published, deadline)
                   VALUES ('Combien de buts ?', 'group', 'choice', '["Oui","Non"]', 6,
                           'Oui', 'exact', 1, '2020-01-01T00:00:00')""",
            )
            question_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO scores (participant_id, bonus_question_id, points)
                   VALUES (?, ?, ?)""",
                (participant_id, question_id, bonus_points),
            )
            await db.execute(
                """INSERT INTO pre_tournament_scores (participant_id, question_key, points)
                   VALUES (?, 'revelation', ?)""",
                (participant_id, pretournament_points),
            )
            await db.commit()

    run(_seed())


def test_bonus_page_shows_total_reconciliation(client, participant):
    _seed_bonus_and_pretournament(
        participant["id"], bonus_points=6, pretournament_points=4
    )

    response = client.get(f"/p/{participant['token']}/bonus")
    assert response.status_code == 200
    html = response.text

    assert "Total bonus : 10 pts" in html
    # Le résumé (PR B6) n'affiche plus le split "Questions bonus / Pré-tournoi" :
    # le pré-tournoi est une phase bonus parmi d'autres dans le détail par phase.
    assert "Questions bonus : 6 pts" not in html
    assert "Phase de groupes : 6 pts" in html
    assert "Pré-tournoi : 4 pts" in html

    # Le total affiché doit rester cohérent avec le périmètre "bonus" du classement
    # (questions bonus + pré-tournoi), sans devenir un nouveau calcul parallèle.
    rankings = run(get_rankings(scope="bonus"))
    ranking_total = next(
        r["total_points"] for r in rankings if r["id"] == participant["id"]
    )
    assert ranking_total == 10


def test_bonus_page_reconciliation_handles_zero_points(client, participant):
    response = client.get(f"/p/{participant['token']}/bonus")
    assert response.status_code == 200
    html = response.text

    assert "Total bonus : 0 pt" in html
    assert "Questions bonus : 0 pt" not in html
    # Rien de résolu encore : aucun détail par phase, message d'attente générique.
    assert "Aucun point bonus calculé pour l'instant." in html


def test_bonus_ranking_tab_does_not_imply_bonus_only(client, participant):
    response = client.get(f"/p/{participant['token']}/classement?view=bonus")
    assert response.status_code == 200
    # Le libellé de l'onglet ne doit plus laisser croire que le périmètre
    # "bonus" exclut le pré-tournoi (il est bien inclus, cf. list-head).
    assert "Bonus uniquement" not in response.text
    assert "Points bonus (pré-tournoi inclus)" in response.text
