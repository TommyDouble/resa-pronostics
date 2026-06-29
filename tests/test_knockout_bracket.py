from app.database import get_db
from app.knockout import ensure_knockout_slots, propagate_from_match
from app.scheduler import _gated_matches
from tests.conftest import run


def _next_match_number():
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT COALESCE(MAX(match_number), 970000) + 1 AS n FROM matches"
            )
            return (await row.fetchone())["n"]

    return run(_get())


def _match(match_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute("SELECT * FROM matches WHERE id=?", (match_id,))
            return dict(await row.fetchone())

    return run(_get())


def _propagate(match_id):
    async def _run():
        async with get_db() as db:
            result = await propagate_from_match(db, match_id)
            await db.commit()
            return result

    return run(_run())


def _prediction_exists(participant_id, match_id):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT 1 FROM predictions WHERE participant_id=? AND match_id=?",
                (participant_id, match_id),
            )
            return await row.fetchone() is not None

    return run(_get())


def test_knockout_slots_parse_seed_style_sources(admin_client):
    async def _seed():
        async with get_db() as db:
            base = (await (await db.execute(
                "SELECT COALESCE(MAX(match_number), 970000) + 1 AS n FROM matches"
            )).fetchone())["n"]
            await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_32', '2099-07-01', '18:00',
                           'France', 'Brésil', 2)""",
                (base,),
            )
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_16', '2099-07-04', '18:00',
                           ?, ?, 2)""",
                (base + 1, f"Vainqueur M{base}", f"Perdant M{base}"),
            )
            await ensure_knockout_slots(db)
            rows = await db.execute(
                """SELECT side, source_kind, source_match_number, source_outcome, is_confirmed
                   FROM knockout_slots WHERE match_id=? ORDER BY side""",
                (cursor.lastrowid,),
            )
            slots = [dict(r) for r in await rows.fetchall()]
            await db.commit()
            return slots

    slots = run(_seed())
    assert slots == [
        {
            "side": "team1",
            "source_kind": "match",
            "source_match_number": slots[0]["source_match_number"],
            "source_outcome": "winner",
            "is_confirmed": 0,
        },
        {
            "side": "team2",
            "source_kind": "match",
            "source_match_number": slots[1]["source_match_number"],
            "source_outcome": "loser",
            "is_confirmed": 0,
        },
    ]


def test_admin_confirms_round_of_32_and_opens_prediction(admin_client, participant):
    number = _next_match_number()

    async def _seed():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           '1er Groupe A', '2e Groupe B', 2)""",
                (number,),
            )
            await db.commit()
            return cursor.lastrowid

    match_id = run(_seed())
    res = admin_client.post(
        f"/admin/matches/{match_id}/teams",
        data={"team1_name": "France", "team2_name": "Brésil", "phase": "round_of_32"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    match = _match(match_id)
    assert match["team1_name"] == "France"
    assert match["team2_name"] == "Brésil"
    assert match["predictions_open"] == 1

    api = admin_client.post(
        f"/api/predictions?token={participant['token']}",
        json={"match_id": match_id, "exact_score_team1": 2, "exact_score_team2": 1, "qualifier_prediction": "team1"},
    )
    assert api.status_code == 200


def test_admin_confirms_single_side_without_opening(admin_client):
    """Fixer une seule équipe ne doit pas ouvrir les pronostics ; l'affiche
    ne s'ouvre qu'une fois les deux côtés confirmés."""
    number = _next_match_number()

    async def _seed():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           'Vainqueur M997', 'Vainqueur M999', 2)""",
                (number,),
            )
            await ensure_knockout_slots(db)
            await db.commit()
            return cursor.lastrowid

    match_id = run(_seed())

    # Fixer le côté connu (team1) : reste fermé.
    res = admin_client.post(
        f"/admin/matches/{match_id}/teams/team1",
        data={"team1_name": "France", "team2_name": "Vainqueur M999", "phase": "round_of_32"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    match = _match(match_id)
    assert match["team1_name"] == "France"
    assert match["predictions_open"] == 0
    assert _slot(match_id, "team1")["is_confirmed"] == 1
    assert _slot(match_id, "team2")["is_confirmed"] == 0

    # Ouvrir est refusé tant que l'adversaire n'est pas confirmé.
    blocked = admin_client.post(
        f"/admin/matches/{match_id}/predictions-open/toggle",
        follow_redirects=False,
    )
    assert blocked.status_code == 303
    assert _match(match_id)["predictions_open"] == 0

    # Fixer le second côté → prêt, mais toujours fermé jusqu'à l'ouverture explicite.
    res2 = admin_client.post(
        f"/admin/matches/{match_id}/teams/team2",
        data={"team1_name": "France", "team2_name": "Brésil", "phase": "round_of_32"},
        follow_redirects=False,
    )
    assert res2.status_code == 303
    match = _match(match_id)
    assert match["team2_name"] == "Brésil"
    assert match["predictions_open"] == 0
    assert _slot(match_id, "team2")["is_confirmed"] == 1

    # Maintenant l'ouverture est autorisée.
    opened = admin_client.post(
        f"/admin/matches/{match_id}/predictions-open/toggle",
        follow_redirects=False,
    )
    assert opened.status_code == 303
    assert _match(match_id)["predictions_open"] == 1


def test_confirm_side_rejects_duplicate_team(admin_client):
    number = _next_match_number()

    async def _seed():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           'France', 'Vainqueur M998', 2)""",
                (number,),
            )
            await ensure_knockout_slots(db)
            await db.commit()
            return cursor.lastrowid

    match_id = run(_seed())
    res = admin_client.post(
        f"/admin/matches/{match_id}/teams/team2",
        data={"team1_name": "France", "team2_name": "France", "phase": "round_of_32"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    # team2 reste le placeholder, slot non confirmé.
    assert _match(match_id)["team2_name"] == "Vainqueur M998"
    assert _slot(match_id, "team2")["is_confirmed"] == 0


def test_knockout_result_propagates_to_next_match_and_opens_when_ready(admin_client):
    source_number = _next_match_number()

    async def _seed():
        async with get_db() as db:
            source = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           'France', 'Brésil', 2, 1)""",
                (source_number,),
            )
            target = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_16', '2099-07-04', '20:00',
                           ?, 'Espagne', 2)""",
                (source_number + 1, f"Vainqueur M{source_number}"),
            )
            await ensure_knockout_slots(db)
            await db.commit()
            return source.lastrowid, target.lastrowid

    source_id, target_id = run(_seed())
    res = admin_client.post(
        f"/admin/resultats/{source_id}",
        data={"score_team1": "2", "score_team2": "0"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    target = _match(target_id)
    assert target["team1_name"] == "France"
    assert target["team2_name"] == "Espagne"
    assert target["predictions_open"] == 1


def test_knockout_extra_time_result_propagates_final_winner(admin_client):
    source_number = _next_match_number()

    async def _seed():
        async with get_db() as db:
            source = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           'France', 'Brésil', 2, 1)""",
                (source_number,),
            )
            target = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight)
                   VALUES (?, 'round_of_16', '2099-07-04', '20:00',
                           ?, 'Espagne', 2)""",
                (source_number + 1, f"Vainqueur M{source_number}"),
            )
            await ensure_knockout_slots(db)
            await db.commit()
            return source.lastrowid, target.lastrowid

    source_id, target_id = run(_seed())
    res = admin_client.post(
        f"/admin/resultats/{source_id}",
        data={
            "score_team1": "1",
            "score_team2": "1",
            "final_score_team1": "1",
            "final_score_team2": "2",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    source = _match(source_id)
    target = _match(target_id)
    assert source["result"] == "draw"
    assert source["qualifier_winner"] == "team2"
    assert target["team1_name"] == "Brésil"
    assert target["predictions_open"] == 1


def test_knockout_correction_conflict_closes_next_match_without_deleting_predictions(
    admin_client, participant
):
    source_number = _next_match_number()

    async def _seed():
        async with get_db() as db:
            source = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           'France', 'Brésil', 2, 1)""",
                (source_number,),
            )
            target = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open)
                   VALUES (?, 'round_of_16', '2099-07-04', '20:00',
                           ?, 'Espagne', 2, 1)""",
                (source_number + 1, f"Vainqueur M{source_number}"),
            )
            await ensure_knockout_slots(db)
            await db.execute(
                "UPDATE matches SET team1_name='Brésil' WHERE id=?",
                (target.lastrowid,),
            )
            await db.execute(
                """UPDATE knockout_slots SET is_confirmed=1
                   WHERE match_id=? AND side='team1'""",
                (target.lastrowid,),
            )
            await db.execute(
                """INSERT INTO predictions
                   (participant_id, match_id, prediction, exact_score_team1, exact_score_team2)
                   VALUES (?, ?, 'team1', 1, 0)""",
                (participant["id"], target.lastrowid),
            )
            await db.commit()
            return source.lastrowid, target.lastrowid

    source_id, target_id = run(_seed())
    res = admin_client.post(
        f"/admin/resultats/{source_id}",
        data={"score_team1": "2", "score_team2": "0"},
        follow_redirects=False,
    )
    assert res.status_code == 303
    target = _match(target_id)
    assert target["team1_name"] == "Brésil"
    assert target["predictions_open"] == 0
    assert _prediction_exists(participant["id"], target_id)


def _slot(match_id, side):
    async def _get():
        async with get_db() as db:
            row = await db.execute(
                "SELECT * FROM knockout_slots WHERE match_id=? AND side=?",
                (match_id, side),
            )
            return dict(await row.fetchone())

    return run(_get())


def test_propagation_does_not_reopen_manually_closed_match(admin_client):
    """Une correction d'un match déjà propagé ne rouvre pas une affiche
    que l'organisateur avait fermée à la main."""
    source_number = _next_match_number()

    async def _seed():
        async with get_db() as db:
            source = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open,
                    score_team1, score_team2, result)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           'France', 'Brésil', 2, 1, 2, 0, 'team1')""",
                (source_number,),
            )
            # Affiche aval déjà confirmée des deux côtés (France propagée + Espagne),
            # ouverte puis refermée manuellement par l'organisateur.
            target = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open)
                   VALUES (?, 'round_of_16', '2099-07-04', '20:00',
                           'France', 'Espagne', 2, 0)""",
                (source_number + 1,),
            )
            await ensure_knockout_slots(db)
            # team1 vient du match source (déjà confirmé), team2 saisie manuelle.
            await db.execute(
                """UPDATE knockout_slots
                   SET source_kind='match', source_match_number=?,
                       source_outcome='winner', is_confirmed=1
                   WHERE match_id=? AND side='team1'""",
                (source_number, target.lastrowid),
            )
            await db.execute(
                "UPDATE knockout_slots SET is_confirmed=1 WHERE match_id=? AND side='team2'",
                (target.lastrowid,),
            )
            await db.commit()
            return source.lastrowid, target.lastrowid

    source_id, target_id = run(_seed())

    result = _propagate(source_id)
    target = _match(target_id)
    assert target["predictions_open"] == 0
    assert result["conflicts"] == []


def test_propagation_skips_already_played_downstream_match(admin_client):
    """Si l'affiche aval est déjà jouée, la propagation ne touche ni son slot
    ni son état d'ouverture, et ne signale pas de conflit."""
    source_number = _next_match_number()

    async def _seed():
        async with get_db() as db:
            source = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open,
                    score_team1, score_team2, result)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           'France', 'Brésil', 2, 1, 2, 0, 'team1')""",
                (source_number,),
            )
            target = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open,
                    score_team1, score_team2, result)
                   VALUES (?, 'round_of_16', '2099-07-04', '20:00',
                           ?, 'Espagne', 2, 0, 1, 0, 'team1')""",
                (source_number + 1, f"Vainqueur M{source_number}"),
            )
            await ensure_knockout_slots(db)
            await db.commit()
            return source.lastrowid, target.lastrowid

    source_id, target_id = run(_seed())

    result = _propagate(source_id)
    slot = _slot(target_id, "team1")
    target = _match(target_id)
    assert slot["is_confirmed"] == 0
    assert target["predictions_open"] == 0
    assert result["conflicts"] == []
    assert result["updates"] == []


def test_scheduler_ignores_closed_knockout_matches(admin_client):
    base = _next_match_number()

    async def _seed_and_find():
        async with get_db() as db:
            closed = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open)
                   VALUES (?, 'round_of_32', '2099-07-01', '18:00',
                           'France', 'Brésil', 2, 0)""",
                (base,),
            )
            opened = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, weight, predictions_open)
                   VALUES (?, 'round_of_32', '2099-07-01', '20:00',
                           'Espagne', 'Allemagne', 2, 1)""",
                (base + 1,),
            )
            matches = await _gated_matches(
                db, "2099-07-01T00:00:00", "2099-07-02T00:00:00"
            )
            await db.commit()
            return closed.lastrowid, opened.lastrowid, {m["id"] for m in matches}

    closed_id, opened_id, match_ids = run(_seed_and_find())
    assert closed_id not in match_ids
    assert opened_id in match_ids
