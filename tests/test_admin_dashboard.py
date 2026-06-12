"""Tableau de bord admin v2 : sections, encodage inline, redirection."""
import uuid
from datetime import datetime, timedelta, timezone

from app.database import get_db
from tests.conftest import run


def _make_match_started_minutes_ago(number, minutes=1):
    ko = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, group_name, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'group', 'Groupe D', ?, ?, 'France', 'Brésil', 1)""",
                (number, ko.strftime("%Y-%m-%d"), ko.strftime("%H:%M")),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _match_result(mid):
    async def _q():
        async with get_db() as db:
            row = await db.execute(
                "SELECT result, score_team1, score_team2 FROM matches WHERE id=?", (mid,)
            )
            return dict(await row.fetchone())

    return run(_q())


def test_dashboard_sections_and_inline_encode_form(admin_client):
    mid = _make_match_started_minutes_ago(950001)
    html = admin_client.get("/admin/dashboard").text
    assert 'data-dash-swap="todo"' in html
    assert 'data-dash-swap="health"' in html
    assert "À faire" in html and "Santé du jeu" in html
    assert "Matchs du jour" in html
    assert "Prochain match" in html and "Paiements" in html and "Bonus" in html
    # Le match commencé sans résultat propose l'encodage inline.
    assert f'action="/admin/resultats/{mid}"' in html
    assert 'name="redirect_to" value="dashboard"' in html


def test_inline_encode_redirects_to_dashboard(admin_client):
    mid = _make_match_started_minutes_ago(950002)
    resp = admin_client.post(
        f"/admin/resultats/{mid}",
        data={"score_team1": 2, "score_team2": 0, "redirect_to": "dashboard"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/dashboard"
    result = _match_result(mid)
    assert result == {"result": "team1", "score_team1": 2, "score_team2": 0}


def test_encode_without_redirect_keeps_results_page(admin_client):
    mid = _make_match_started_minutes_ago(950003)
    resp = admin_client.post(
        f"/admin/resultats/{mid}",
        data={"score_team1": 1, "score_team2": 1},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/resultats"
