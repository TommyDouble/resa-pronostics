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


def _seed_group_match(number, match_date, kickoff_time, team1, team2):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, group_name, match_date, kickoff_time,
                                        team1_name, team2_name, weight)
                   VALUES (?, 'group', 'Groupe D', ?, ?, ?, ?, 1)""",
                (number, match_date, kickoff_time, team1, team2),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _cleanup_matches(numbers):
    async def _clean():
        async with get_db() as db:
            placeholders = ",".join("?" for _ in numbers)
            await db.execute(
                f"DELETE FROM matches WHERE match_number IN ({placeholders})", numbers
            )
            await db.commit()

    run(_clean())


def test_dashboard_sections_and_inline_encode_form(admin_client):
    mid = _make_match_started_minutes_ago(950001)
    html = admin_client.get("/admin/dashboard").text
    assert 'data-dash-swap="todo"' in html
    assert 'data-dash-swap="health"' in html
    assert "À faire" in html and "Santé du jeu" in html
    assert "Résultats à encoder" in html and "Échéances" in html
    assert "Matchs de la journée sportive" in html
    assert "Prochain match" in html and "Paiements" in html and "Bonus" in html
    # Le match commencé sans résultat propose l'encodage inline.
    assert f'action="/admin/resultats/{mid}"' in html
    assert 'name="redirect_to" value="dashboard"' in html


def test_pending_results_card_lists_late_match_and_removes_alert(admin_client, monkeypatch):
    monkeypatch.setenv("FAKE_NOW_UTC", "2020-01-01T12:00:00")
    mid = _seed_group_match(971001, "2019-12-30", "10:00:00", "PendingAlpha", "PendingBeta")
    try:
        html = admin_client.get("/admin/dashboard").text
        assert "Résultats à encoder" in html
        assert "PendingAlpha" in html and "PendingBeta" in html
        # Un seul formulaire d'encodage pour ce match sur toute la page.
        assert html.count(f'action="/admin/resultats/{mid}"') == 1
        assert 'name="redirect_to" value="dashboard"' in html
        assert "admin-alert" not in html
    finally:
        _cleanup_matches([971001])


def test_pending_results_card_empty_state(admin_client, monkeypatch):
    monkeypatch.setenv("FAKE_NOW_UTC", "2021-06-15T12:00:00")
    html = admin_client.get("/admin/dashboard").text
    assert "Résultats à encoder" in html
    assert "Rien à encoder" in html


def test_today_match_defers_encode_form_to_pending_card(admin_client, monkeypatch):
    # Match à la fois dans la fenêtre « journée sportive » ET en retard (>2h) :
    # un seul formulaire d'encodage doit apparaître sur toute la page (carte
    # « Résultats à encoder »), pas de doublon dans « Matchs de la journée sportive ».
    monkeypatch.setenv("FAKE_NOW_UTC", "2020-01-01T12:00:00")
    mid = _seed_group_match(971002, "2020-01-01", "09:00:00", "DedupAlpha", "DedupBeta")
    try:
        html = admin_client.get("/admin/dashboard").text
        assert "Matchs de la journée sportive" in html
        assert "DedupAlpha" in html and "DedupBeta" in html
        assert html.count(f'action="/admin/resultats/{mid}"') == 1
        assert "À encoder ci-dessus" in html
    finally:
        _cleanup_matches([971002])


def test_echeances_card_shows_late_and_future_deadlines(admin_client, participant, monkeypatch):
    monkeypatch.setenv("FAKE_NOW_UTC", "2020-01-01T12:00:00")
    late_id = _seed_group_match(971003, "2019-12-30", "10:00:00", "EcheanceLate1", "EcheanceLate2")
    future_id = _seed_group_match(971004, "2020-01-05", "10:00:00", "EcheanceFuture1", "EcheanceFuture2")
    try:
        html = admin_client.get("/admin/dashboard").text
        assert "Échéances" in html
        assert "EcheanceLate1" in html and "EcheanceLate2" in html
        assert "EcheanceFuture1" in html and "EcheanceFuture2" in html
        assert "/admin/communications" in html
    finally:
        _cleanup_matches([971003, 971004])


def test_echeances_card_shows_real_total_beyond_visible_three(admin_client, participant, monkeypatch):
    # 4 échéances dépassées distinctes : le gros compteur doit afficher le total
    # réel (4), pas la taille de la liste de détail tronquée à 3.
    monkeypatch.setenv("FAKE_NOW_UTC", "2020-01-01T12:00:00")
    numbers = [971020, 971021, 971022, 971023]
    dates = ["2019-12-26", "2019-12-27", "2019-12-28", "2019-12-29"]
    try:
        for number, day in zip(numbers, dates):
            _seed_group_match(number, day, "10:00:00", f"Ech{number}A", f"Ech{number}B")
        html = admin_client.get("/admin/dashboard").text
        assert "4<small> dépassées</small>" in html
        assert "+1 autre" in html
    finally:
        _cleanup_matches(numbers)


def test_pending_results_overflow_links_instead_of_duplicating_form(admin_client, monkeypatch):
    # 9 matchs en retard, tous dans la fenêtre « journée sportive » du 2020-01-01 :
    # seuls les 8 plus anciens sont dans la carte « Résultats à encoder ». Le 9e
    # (le moins ancien, donc tronqué) ne doit ni afficher un second formulaire, ni
    # afficher « À encoder ci-dessus » (il n'est pas réellement visible au-dessus) —
    # mais un lien clair vers /admin/resultats.
    monkeypatch.setenv("FAKE_NOW_UTC", "2020-01-01T12:00:00")
    numbers = list(range(971030, 971039))  # 9 matchs
    base_kickoff = datetime(2020, 1, 1, 8, 5)
    kickoffs = [(base_kickoff + timedelta(minutes=10 * i)).strftime("%H:%M:%S") for i in range(9)]
    mids = []
    try:
        for i, (number, kickoff) in enumerate(zip(numbers, kickoffs)):
            mid = _seed_group_match(number, "2020-01-01", kickoff, f"Ovf{i}A", f"Ovf{i}B")
            mids.append(mid)
        html = admin_client.get("/admin/dashboard").text
        shown_mid = mids[0]  # le plus ancien (08:05) -> parmi les 8 affichés
        overflow_mid = mids[-1]  # le moins ancien (09:25) -> tronqué
        assert f'action="/admin/resultats/{shown_mid}"' in html
        assert html.count(f'action="/admin/resultats/{overflow_mid}"') == 0
        assert "Ovf8A" in html and "Ovf8B" in html  # la ligne existe bien dans le planning
        assert "À encoder dans Résultats →" in html
    finally:
        _cleanup_matches(numbers)


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
