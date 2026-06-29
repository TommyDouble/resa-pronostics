from app.database import get_db, init_db
from app.routers.pages import _zero_points_reason
from tests.conftest import run


def _ko_match(score1, score2, qualifier=None, result="draw"):
    return {
        "phase": "round_of_16",
        "score_team1": score1,
        "score_team2": score2,
        "final_score_team1": score1,
        "final_score_team2": score2,
        "qualifier_winner": qualifier,
        "result": result,
        "team1_name": "Espagne",
        "team2_name": "Allemagne",
        "weight": 2,
    }


def test_zero_reason_no_prediction():
    match = _ko_match(1, 1, qualifier="team1")
    assert _zero_points_reason(None, match) == "Pas de prono"
    assert _zero_points_reason({"exact_score_team1": None}, match) == "Pas de prono"


def test_zero_reason_wrong_qualifier():
    # Prono nul 1-1 + team1, mais team2 qualifiée → mauvais qualifié.
    match = _ko_match(1, 1, qualifier="team2")
    pred = {"exact_score_team1": 1, "exact_score_team2": 1, "qualifier_prediction": "team1"}
    assert _zero_points_reason(pred, match) == "Mauvais qualifié"


def test_zero_reason_wrong_outcome():
    # Prono victoire team1 (2-1) mais team2 qualifiée → mauvaise issue.
    match = _ko_match(0, 1, qualifier="team2", result="team2")
    pred = {"exact_score_team1": 2, "exact_score_team2": 1, "qualifier_prediction": None}
    assert _zero_points_reason(pred, match) == "Mauvaise issue"


def open_knockout_predictions(match_id):
    async def _open():
        async with get_db() as db:
            await db.execute(
                "UPDATE matches SET predictions_open=1 WHERE id=?", (match_id,)
            )
            await db.commit()

    return run(_open())


def get_match_score(participant_id, match_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT points FROM scores WHERE participant_id=? AND match_id=?",
                (participant_id, match_id),
            )
            result = await row.fetchone()
            return result["points"] if result else None

    return run(_get())


def get_match_result(match_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
            result = await row.fetchone()
            return dict(result) if result else None

    return run(_get())


def test_final_score_columns_are_migrated_idempotently(admin_client):
    async def _check():
        async with get_db() as db:
            columns = await db.execute("PRAGMA table_info(matches)")
            names = {row["name"] for row in await columns.fetchall()}
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (980001, 'group', '2099-06-01', '20:00',
                           'France', 'Brésil', 1, 3, 1, 'team1')"""
            )
            await db.commit()
            match_id = cursor.lastrowid
        await init_db()
        async with get_db() as db:
            row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
            match = dict(await row.fetchone())
        return names, match

    columns, match = run(_check())
    assert {"final_score_team1", "final_score_team2"}.issubset(columns)
    assert match["final_score_team1"] == 3
    assert match["final_score_team2"] == 1


def create_knockout_match(participant_id):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_16', '2099-07-01', '20:00',
                           'Espagne', 'Allemagne', 2)""",
                (930000 + participant_id,),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def test_knockout_prediction_blocked_until_opened(admin_client, participant):
    match_id = create_knockout_match(participant["id"])

    blocked = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 1,
            "exact_score_team2": 0,
        },
    )
    assert blocked.status_code == 403
    assert "phase finale" in blocked.json()["detail"]


def test_knockout_draw_result_requires_qualifier_and_scores_by_winner(admin_client, participant):
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)

    response = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 2,
            "exact_score_team2": 2,
            "qualifier_prediction": "team1",
        },
    )
    assert response.status_code == 200

    missing_qualifier = admin_client.post(
        f"/admin/resultats/{match_id}",
        data={"score_team1": "2", "score_team2": "2"},
        follow_redirects=False,
    )
    assert missing_qualifier.status_code == 303
    assert get_match_result(match_id)["result"] is None

    wrong_qualifier = admin_client.post(
        f"/admin/resultats/{match_id}",
        data={"score_team1": "2", "score_team2": "2", "qualifier_winner": "team2"},
        follow_redirects=False,
    )
    assert wrong_qualifier.status_code == 303
    assert get_match_score(participant["id"], match_id) == 0

    corrected = admin_client.post(
        f"/admin/resultats/{match_id}/correct",
        data={"score_team1": "2", "score_team2": "2", "qualifier_winner": "team1"},
        follow_redirects=False,
    )
    assert corrected.status_code == 303
    assert get_match_score(participant["id"], match_id) == 6
    assert get_match_result(match_id)["qualifier_winner"] == "team1"


def test_knockout_extra_time_final_score_deduces_qualifier_and_keeps_90_score_exact(
    admin_client, participant
):
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)

    response = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 1,
            "exact_score_team2": 1,
            "qualifier_prediction": "team1",
        },
    )
    assert response.status_code == 200

    encoded = admin_client.post(
        f"/admin/resultats/{match_id}",
        data={
            "score_team1": "1",
            "score_team2": "1",
            "final_score_team1": "2",
            "final_score_team2": "1",
        },
        follow_redirects=False,
    )
    assert encoded.status_code == 303

    match = get_match_result(match_id)
    assert match["result"] == "draw"
    assert match["score_team1"] == 1
    assert match["score_team2"] == 1
    assert match["final_score_team1"] == 2
    assert match["final_score_team2"] == 1
    assert match["qualifier_winner"] == "team1"
    assert get_match_score(participant["id"], match_id) == 6


def test_knockout_penalties_keep_final_score_level_and_require_qualifier(
    admin_client, participant
):
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)

    response = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 1,
            "exact_score_team2": 1,
            "qualifier_prediction": "team2",
        },
    )
    assert response.status_code == 200

    encoded = admin_client.post(
        f"/admin/resultats/{match_id}",
        data={
            "score_team1": "1",
            "score_team2": "1",
            "final_score_team1": "1",
            "final_score_team2": "1",
            "qualifier_winner": "team2",
        },
        follow_redirects=False,
    )
    assert encoded.status_code == 303

    match = get_match_result(match_id)
    assert match["result"] == "draw"
    assert match["final_score_team1"] == 1
    assert match["final_score_team2"] == 1
    assert match["qualifier_winner"] == "team2"
    assert get_match_score(participant["id"], match_id) == 6


def test_knockout_final_score_cannot_be_lower_than_90(admin_client, participant):
    match_id = create_knockout_match(participant["id"])

    rejected = admin_client.post(
        f"/admin/resultats/{match_id}",
        data={
            "score_team1": "2",
            "score_team2": "2",
            "final_score_team1": "1",
            "final_score_team2": "2",
            "qualifier_winner": "team2",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    assert get_match_result(match_id)["result"] is None


def test_decisive_prono_on_et_match_scores_winner_not_exact(admin_client, participant):
    """Prono 2-1 (victoire team1). Le match finit 1-1 puis team1 qualifiée a.p.
    → bon qualifié (+4 pts) mais pas de score exact (2 ≠ 1 à 90')."""
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)

    response = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 2,
            "exact_score_team2": 1,
            "qualifier_prediction": "team1",
        },
    )
    assert response.status_code == 200

    encoded = admin_client.post(
        f"/admin/resultats/{match_id}",
        data={
            "score_team1": "1",
            "score_team2": "1",
            "final_score_team1": "2",
            "final_score_team2": "1",
        },
        follow_redirects=False,
    )
    assert encoded.status_code == 303

    match = get_match_result(match_id)
    assert match["result"] == "draw"
    assert match["qualifier_winner"] == "team1"
    # Bon qualifié (team1) à ×2 = 4 pts, mais score 90' faux (2-1 ≠ 1-1) → pas de bonus.
    assert get_match_score(participant["id"], match_id) == 4


def test_prediction_update_from_draw_to_decisive_keeps_qualifier(admin_client, participant):
    """Passer d'un prono nul + qualifié à un prono décisif conserve le qualifier_prediction
    (l'API winner-first exige toujours un qualifié pour les matchs knockout)."""
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)

    first = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 1,
            "exact_score_team2": 1,
            "qualifier_prediction": "team1",
        },
    )
    assert first.status_code == 200

    second = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 2,
            "exact_score_team2": 1,
            "qualifier_prediction": "team1",
        },
    )
    assert second.status_code == 200

    async def _qualifier():
        async with get_db() as db:
            row = await db.execute(
                "SELECT qualifier_prediction FROM predictions WHERE participant_id=? AND match_id=?",
                (participant["id"], match_id),
            )
            return (await row.fetchone())["qualifier_prediction"]

    assert run(_qualifier()) == "team1"

    admin_client.post(
        f"/admin/resultats/{match_id}",
        data={"score_team1": "2", "score_team2": "1"},
        follow_redirects=False,
    )
    # Victoire team1 2-1 pronostiquée et réalisée → 6 pts (×2 + score exact).
    assert get_match_score(participant["id"], match_id) == 6


def test_knockout_result_display_shows_extra_time_detail(admin_client, participant):
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)
    admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 1,
            "exact_score_team2": 1,
            "qualifier_prediction": "team1",
        },
    )
    admin_client.post(
        f"/admin/resultats/{match_id}",
        data={
            "score_team1": "1",
            "score_team2": "1",
            "final_score_team1": "2",
            "final_score_team2": "1",
        },
        follow_redirects=False,
    )
    async def _make_past():
        async with get_db() as db:
            await db.execute(
                "UPDATE matches SET match_date='2000-01-01', kickoff_time='12:00' WHERE id=?",
                (match_id,),
            )
            await db.commit()

    run(_make_past())

    html = admin_client.get(f"/p/{participant['token']}/pronos?section=round_of_16").text
    assert "2–1 a.p." in html
    assert "90&#39; : 1–1" in html or "90' : 1–1" in html
    assert "Espagne qualifiée" in html


def test_knockout_decisive_requires_qualifier(admin_client, participant):
    """Un prono knockout décisif sans qualifier_prediction est refusé (400)."""
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)

    response = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 2,
            "exact_score_team2": 0,
        },
    )
    assert response.status_code == 400
    assert "qualifié" in response.json()["detail"].lower()


def test_knockout_decisive_incoherent_qualifier_rejected(admin_client, participant):
    """Un prono knockout 2-0 avec qualifié=team2 (incohérent) est refusé (400)."""
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)

    response = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 2,
            "exact_score_team2": 0,
            "qualifier_prediction": "team2",
        },
    )
    assert response.status_code == 400
    assert "incohérent" in response.json()["detail"].lower()


def test_knockout_decisive_coherent_qualifier_accepted(admin_client, participant):
    """Un prono knockout 2-0 avec qualifié=team1 (cohérent) est accepté."""
    match_id = create_knockout_match(participant["id"])
    open_knockout_predictions(match_id)

    response = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={
            "match_id": match_id,
            "exact_score_team1": 2,
            "exact_score_team2": 0,
            "qualifier_prediction": "team1",
        },
    )
    assert response.status_code == 200
