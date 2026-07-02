"""Accueil participant et dashboard admin partagent le même lot 9 h–8 h 59."""
import app.routers.admin as admin_routes
import app.routers.pages as page_routes

from app.database import get_db
from tests.conftest import run


MATCH_NUMBERS = (963001, 963002, 963003)


def _seed_window():
    async def _seed():
        async with get_db() as db:
            values = (
                (MATCH_NUMBERS[0], "2035-06-01", "10:00:00", "Dans la journée"),
                (MATCH_NUMBERS[1], "2035-06-02", "02:00:00", "Après minuit"),
                (MATCH_NUMBERS[2], "2035-06-02", "08:00:00", "Hors fenêtre"),
            )
            for number, day, kickoff, team in values:
                await db.execute(
                    """INSERT INTO matches
                       (match_number, phase, match_date, kickoff_time,
                        team1_name, team2_name, weight)
                       VALUES (?, 'group', ?, ?, ?, 'Brésil', 1)""",
                    (number, day, kickoff, team),
                )
            await db.commit()
    run(_seed())


def _cleanup_window():
    async def _clean():
        async with get_db() as db:
            await db.execute(
                "DELETE FROM matches WHERE match_number IN (?,?,?)", MATCH_NUMBERS
            )
            await db.commit()
    run(_clean())


def test_home_and_admin_show_same_sporting_day_window(
    client, admin_client, participant, monkeypatch
):
    _seed_window()
    monkeypatch.setattr(page_routes, "current_sporting_day", lambda: "2035-06-01")
    monkeypatch.setattr(admin_routes, "current_sporting_day", lambda: "2035-06-01")
    try:
        home = client.get(f"/p/{participant['token']}").text
        dashboard = admin_client.get("/admin/dashboard").text
        assert "Aujourd’hui" in home
        assert "Matchs de la journée sportive" in dashboard
        assert "Dans la journée" in home
        assert "Après minuit" in home
        assert "Hors fenêtre" not in home
        assert "04:00" in home  # le jour civil du match nocturne est explicite
        # Sur le dashboard, on ne vérifie la fenêtre 9h-8h59 que dans la carte
        # planning : le match « Hors fenêtre » peut légitimement apparaître par
        # ailleurs (carte « Échéances », qui liste les prochaines deadlines toutes
        # dates confondues, indépendamment de la journée sportive courante).
        schedule_start = dashboard.index("Matchs de la journée sportive")
        schedule_end = dashboard.index("dash-grid", schedule_start)
        schedule = dashboard[schedule_start:schedule_end]
        assert "Dans la journée" in schedule
        assert "Après minuit" in schedule
        assert "Hors fenêtre" not in schedule
        assert "04:00" in schedule
    finally:
        _cleanup_window()
