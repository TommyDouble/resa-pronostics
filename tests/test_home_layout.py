"""Contrats de rendu de l'accueil participant allégé."""
from pathlib import Path

import app.routers.pages as page_routes
from app.database import get_db
from tests.conftest import run


SPORTING_DAY = "2035-06-01"


def _reset_matches():
    async def _reset():
        async with get_db() as db:
            await db.execute("DELETE FROM matches")
            await db.commit()

    run(_reset())


def _seed_match(
    number,
    *,
    day=SPORTING_DAY,
    kickoff="19:00:00",
    team1="France",
    team2="Brésil",
    top=False,
    result=None,
    score=None,
):
    async def _seed():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time,
                    team1_name, team2_name, is_top_match, weight,
                    score_team1, score_team2, result)
                   VALUES (?, 'group', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    number,
                    day,
                    kickoff,
                    team1,
                    team2,
                    int(top),
                    2 if top else 1,
                    score[0] if score else None,
                    score[1] if score else None,
                    result,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_seed())


def _predict(participant_id, match_id, score=(2, 1), prediction="team1"):
    async def _save():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions
                   (participant_id, match_id, prediction,
                    exact_score_team1, exact_score_team2)
                   VALUES (?, ?, ?, ?, ?)""",
                (participant_id, match_id, prediction, score[0], score[1]),
            )
            await db.commit()

    run(_save())


def _stable_home(monkeypatch, *, urgent_minutes=None):
    monkeypatch.setattr(page_routes, "current_sporting_day", lambda: SPORTING_DAY)

    async def closed_pre_tournament(db, participant_id):
        return {
            "open": False,
            "complete": True,
            "filled_count": 0,
            "question_count": 5,
            "deadline": None,
        }

    original_context = page_routes._get_participant_context

    async def context_without_bonus(token, db, active_nav="home"):
        context = await original_context(token, db, active_nav)
        context["pending_bonus"] = 0
        return context

    monkeypatch.setattr(page_routes, "_pt_status", closed_pre_tournament)
    monkeypatch.setattr(page_routes, "_get_participant_context", context_without_bonus)
    if urgent_minutes is not None:
        monkeypatch.setattr(page_routes, "_minutes_until", lambda match: urgent_minutes)


def _match_list(html):
    start = html.index("data-home-match-list")
    return html[start:html.index("</section>", start)]


def test_completed_predictions_replace_redundant_statuses(
    client, participant, monkeypatch
):
    _reset_matches()
    _stable_home(monkeypatch)
    match_id = _seed_match(
        966001, team1="États-Unis", team2="Australie", top=True
    )
    _predict(participant["id"], match_id)

    html = client.get(f"/p/{participant['token']}").text
    matches = _match_list(html)

    assert "data-home-status" not in html
    assert 'class="home-match-list"' in matches
    assert "home-next-countdown" in matches
    assert matches.count("data-countdown=") == 1
    assert "Prono" in matches and "2–1" in matches
    assert "Pronostic enregistré :" in matches
    assert "enregistré ✓" not in matches
    assert 'class="dot ' not in matches
    assert "×1" not in matches
    assert matches.count("★ ×2") == 1

    assert html.count("États-Unis") == 1
    assert html.count("Australie") == 1


def test_urgent_match_is_the_only_match_cta(client, participant, monkeypatch):
    _reset_matches()
    _stable_home(monkeypatch, urgent_minutes=25)
    match_id = _seed_match(966002)

    html = client.get(f"/p/{participant['token']}").text
    matches = _match_list(html)

    assert "data-home-urgent" in matches
    assert "home-match-cta" in matches
    assert "home-next-countdown" in matches
    assert "home-urgent-countdown" not in matches
    assert matches.count("data-countdown=") == 1
    assert 'class="urgency"' not in html
    assert "home-section-action" not in matches
    assert "prono manquant" not in matches
    assert "data-home-status" not in html
    assert html.count(f"match={match_id}#match-{match_id}") == 1


def test_nonurgent_missing_matches_use_list_header_action(
    client, participant, monkeypatch
):
    _reset_matches()
    _stable_home(monkeypatch)
    first_match = _seed_match(966003, kickoff="19:00:00")
    _predict(participant["id"], first_match)
    _seed_match(966004, kickoff="21:00:00", team1="Maroc", team2="Japon")
    _seed_match(966010, kickoff="22:00:00", team1="Suisse", team2="Canada")

    html = client.get(f"/p/{participant['token']}").text
    matches = _match_list(html)

    assert "2 pronos manquants" in matches
    assert "home-section-action" in matches
    assert ">Compléter</a>" in matches
    assert matches.count("À pronostiquer") == 2
    assert "home-next-countdown" in matches
    assert matches.count("data-countdown=") == 1
    assert "data-home-status" not in html


def test_single_nonurgent_missing_prediction_uses_singular(
    client, participant, monkeypatch
):
    _reset_matches()
    _stable_home(monkeypatch)
    _seed_match(966009)

    html = client.get(f"/p/{participant['token']}").text
    matches = _match_list(html)

    assert "1 prono manquant" in matches
    assert "1 pronos manquants" not in matches


def test_future_match_is_shown_once_when_today_is_empty(
    client, participant, monkeypatch
):
    _reset_matches()
    _stable_home(monkeypatch)
    _seed_match(
        966005,
        day="2035-06-03",
        team1="États-Unis",
        team2="Australie",
        top=True,
    )

    html = client.get(f"/p/{participant['token']}").text

    assert "data-home-match-list" not in html
    assert "data-home-next-away" in html
    assert "data-home-status" not in html
    assert html.count("data-countdown=") == 1
    assert html.count("États-Unis") == 1
    assert html.count("Australie") == 1
    assert html.count("★ ×2") == 1


def test_end_of_calendar_keeps_a_compact_message(client, participant, monkeypatch):
    _reset_matches()
    _stable_home(monkeypatch)

    html = client.get(f"/p/{participant['token']}").text

    assert "data-home-calendar-end" in html
    assert "data-home-status" not in html
    assert "Plus aucun match au calendrier" in html
    assert "data-home-match-list" not in html


def test_live_waiting_and_final_states_keep_text_without_dots(
    client, participant, monkeypatch
):
    _reset_matches()
    _stable_home(monkeypatch)
    live_id = _seed_match(966006, team1="Live", team2="France")
    waiting_id = _seed_match(966007, kickoff="20:00:00", team1="Waiting", team2="France")
    done_id = _seed_match(
        966008,
        kickoff="21:00:00",
        team1="Done",
        team2="France",
        result="team1",
        score=(3, 1),
    )

    monkeypatch.setattr(page_routes, "_is_locked", lambda match: True)
    monkeypatch.setattr(
        page_routes,
        "_live_state",
        lambda match: "live" if match["id"] == live_id else (
            "awaiting" if match["id"] == waiting_id else "done"
        ),
    )

    html = client.get(f"/p/{participant['token']}").text
    matches = _match_list(html)

    assert "En cours" in matches
    assert "Résultat en attente" in matches
    assert "Terminé" in matches and "3–1" in matches
    assert 'class="dot ' not in matches
    assert f'/match/{done_id}' in matches


def test_home_sections_stay_in_priority_order():
    source = Path("app/templates/home.html").read_text()
    assert source.index("data-home-match-list") < source.index("data-home-reveal")
    assert source.index("data-home-reveal") < source.index("Mini classement")
    assert source.index("Mini classement") < source.index("data-push-promo")
    assert "home-mini-ranking" in source
    assert "home-status" not in source
    assert "home-urgent-countdown" not in source
    assert "Il te manque" not in source
    assert "Tu mènes avec" not in source


def test_countdown_formatting_is_stable_until_the_last_hour():
    source = Path("app/static/js/resa.js").read_text()
    assert "Coup d’envoi !" in source
    assert "else if (h > 0)" in source
    assert "h + 'h ' + pad(m) + 'min'" in source
    assert "pad(m) + 'min ' + pad(s) + 's'" in source
