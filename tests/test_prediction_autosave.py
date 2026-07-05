"""Contrat API de la sauvegarde des pronostics (autosave côté client).

Le client JS (initPredictionScores dans resa.js) repose sur ces garanties :
- une sauvegarde réussie renvoie 200 + success=True (déclenche le badge « Enregistré ») ;
- une re-sauvegarde écrase la précédente (UPSERT, la dernière requête traitée gagne) ;
- un match verrouillé pendant la saisie renvoie 403 avec un détail explicite
  (affiché dans la carte, jamais un échec silencieux) ;
- une entrée invalide renvoie 4xx avec un détail (idem).

Le comportement purement navigateur (débounce, garde de séquence saveSeq qui
ignore les réponses désordonnées) vit dans des closures de resa.js : sans
runner JS/Playwright dans ce projet, il n'est pas testable proprement ici.
"""
from app.database import get_db
from tests.conftest import run


def create_group_match(match_number, match_date="2099-06-01"):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'group', ?, '20:00', 'France', 'Brésil', 1)""",
                (match_number, match_date),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def get_prediction(participant_id, match_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM predictions WHERE participant_id=? AND match_id=?",
                (participant_id, match_id),
            )
            result = await row.fetchone()
            return dict(result) if result else None

    return run(_get())


def test_save_prediction_success_contract(client, participant):
    match_id = create_group_match(940000 + participant["id"])

    response = client.post(
        f"/api/predictions?token={participant['token']}",
        json={"match_id": match_id, "exact_score_team1": 2, "exact_score_team2": 0},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["prediction"] == "team1"

    saved = get_prediction(participant["id"], match_id)
    assert saved["exact_score_team1"] == 2
    assert saved["exact_score_team2"] == 0
    assert saved["prediction"] == "team1"


def test_resave_overwrites_previous_prediction(client, participant):
    match_id = create_group_match(941000 + participant["id"])
    token = participant["token"]

    first = client.post(
        f"/api/predictions?token={token}",
        json={"match_id": match_id, "exact_score_team1": 1, "exact_score_team2": 0},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/predictions?token={token}",
        json={"match_id": match_id, "exact_score_team1": 0, "exact_score_team2": 3},
    )
    assert second.status_code == 200
    assert second.json()["prediction"] == "team2"

    saved = get_prediction(participant["id"], match_id)
    assert saved["exact_score_team1"] == 0
    assert saved["exact_score_team2"] == 3
    assert saved["prediction"] == "team2"


def test_locked_match_rejected_with_explicit_detail(client, participant):
    # Coup d'envoi passé → verrouillé : la sauvegarde pendant la saisie doit
    # échouer avec un message affichable, pas silencieusement.
    match_id = create_group_match(942000 + participant["id"], match_date="2000-06-01")

    response = client.post(
        f"/api/predictions?token={participant['token']}",
        json={"match_id": match_id, "exact_score_team1": 1, "exact_score_team2": 1},
    )
    assert response.status_code == 403
    assert "verrouillé" in response.json()["detail"]
    assert get_prediction(participant["id"], match_id) is None


def test_invalid_scores_rejected(client, participant):
    match_id = create_group_match(943000 + participant["id"])
    token = participant["token"]

    missing = client.post(
        f"/api/predictions?token={token}",
        json={"match_id": match_id, "exact_score_team1": 1},
    )
    assert missing.status_code == 400
    assert missing.json()["detail"]

    out_of_range = client.post(
        f"/api/predictions?token={token}",
        json={"match_id": match_id, "exact_score_team1": 31, "exact_score_team2": 0},
    )
    assert out_of_range.status_code == 400
    assert "0 et 30" in out_of_range.json()["detail"]


def test_invalid_token_rejected(client, participant):
    match_id = create_group_match(944000 + participant["id"])

    response = client.post(
        "/api/predictions?token=not-a-real-token",
        json={"match_id": match_id, "exact_score_team1": 1, "exact_score_team2": 0},
    )
    assert response.status_code == 403
