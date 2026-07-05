"""Pagination serveur de /admin/pronostics (vue 'matches' uniquement)."""
import uuid

import pytest

import app.routers.admin as admin_routes
from app.database import get_db
from tests.conftest import run


def _seed_predictions_for_one_participant(n_matches):
    """Crée un participant dédié + n_matches matchs distincts + une prédiction par
    match pour ce participant. Filtrer par son participant_id isole totalement le
    test des données laissées par d'autres tests (DB partagée entre tests).
    Retourne (participant_id, match_ids) pour permettre un cleanup explicite."""
    token = str(uuid.uuid4())

    async def _seed():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO participants (name, first_name, last_name, email, token, is_confirmed)
                   VALUES (?,?,?,?,?,1)""",
                (f"Pager {token[:8]}", "Pager", token[:8], f"{token}@test.local", token),
            )
            participant_id = cursor.lastrowid

            number_row = await db.execute(
                "SELECT COALESCE(MAX(match_number), 0) + 1000 AS number FROM matches"
            )
            base_number = (await number_row.fetchone())["number"]

            match_ids = []
            for j in range(n_matches):
                cursor = await db.execute(
                    """INSERT INTO matches
                       (match_number, phase, match_date, kickoff_time, team1_name, team2_name)
                       VALUES (?, 'group', '2030-01-01', '12:00', 'Equipe A', 'Equipe B')""",
                    (base_number + j,),
                )
                match_ids.append(cursor.lastrowid)

            for mid in match_ids:
                await db.execute(
                    """INSERT INTO predictions (participant_id, match_id, prediction, submitted_at)
                       VALUES (?, ?, 'team1', '2030-01-01 12:00:00')""",
                    (participant_id, mid),
                )
            await db.commit()
            return participant_id, match_ids

    return run(_seed())


def _cleanup_participant_and_matches(participant_id, match_ids):
    """Supprime le participant (cascade sur ses prédictions) et les matchs fictifs
    créés pour le test, pour ne pas laisser de données de test dans la DB partagée."""
    async def _clean():
        async with get_db() as db:
            await db.execute("DELETE FROM participants WHERE id=?", (participant_id,))
            if match_ids:
                placeholders = ",".join("?" for _ in match_ids)
                await db.execute(
                    f"DELETE FROM matches WHERE id IN ({placeholders})", match_ids
                )
            await db.commit()

    run(_clean())


def _row_count(html):
    return html.count('class="s"')


@pytest.fixture()
def paged_participant(monkeypatch):
    """6 prédictions pour un participant dédié + PREDICTIONS_PAGE_SIZE=2 (3 pages),
    nettoyé automatiquement après le test."""
    monkeypatch.setattr(admin_routes, "PREDICTIONS_PAGE_SIZE", 2)
    participant_id, match_ids = _seed_predictions_for_one_participant(6)
    yield participant_id
    _cleanup_participant_and_matches(participant_id, match_ids)


def test_matches_view_page_1_has_next_link_only(admin_client, paged_participant):
    pid = paged_participant
    response = admin_client.get(f"/admin/pronostics?view=matches&participant_id={pid}")
    assert response.status_code == 200
    assert _row_count(response.text) == 2
    assert "Page suivante" in response.text
    assert "Page précédente" not in response.text
    assert f"participant_id={pid}&phase=all&page=2" in response.text


def test_matches_view_page_2_has_prev_and_next_links(admin_client, paged_participant):
    pid = paged_participant
    response = admin_client.get(f"/admin/pronostics?view=matches&participant_id={pid}&page=2")
    assert response.status_code == 200
    assert _row_count(response.text) == 2
    assert "Page précédente" in response.text
    assert "Page suivante" in response.text
    assert f"participant_id={pid}&phase=all&page=1" in response.text
    assert f"participant_id={pid}&phase=all&page=3" in response.text


def test_matches_view_page_3_has_prev_only(admin_client, paged_participant):
    pid = paged_participant
    response = admin_client.get(f"/admin/pronostics?view=matches&participant_id={pid}&page=3")
    assert response.status_code == 200
    assert _row_count(response.text) == 2
    assert "Page précédente" in response.text
    assert "Page suivante" not in response.text
    assert f"participant_id={pid}&phase=all&page=2" in response.text


def test_matches_filter_form_has_no_hidden_page_field(admin_client):
    response = admin_client.get("/admin/pronostics?view=matches")
    assert response.status_code == 200
    assert 'name="page"' not in response.text


def test_pre_tournament_and_bonus_views_have_no_pagination_links(admin_client):
    for view in ("pre_tournament", "bonus"):
        response = admin_client.get(f"/admin/pronostics?view={view}")
        assert response.status_code == 200
        assert "Page suivante" not in response.text
        assert "Page précédente" not in response.text
