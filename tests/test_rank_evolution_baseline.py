"""Flèches d'évolution (piste B) : déterministe, basée sur la journée de kickoff.

Aucun snapshot : la référence est recalculée à chaque affichage à partir des
dates de coup d'envoi (fuseau d'affichage), comme la page Pronos. La flèche
montre le mouvement provoqué par les matchs de la dernière journée jouée.

get_rank_evolution est global (tous les matchs de la base). La base de test
étant partagée, on isole chaque scénario en mettant de côté matchs / scores /
pronostics le temps du test, puis on les restaure.
"""
import contextlib
from pathlib import Path
import uuid

import app.routers.pages as page_routes
from app.database import get_db
from app.scoring import (
    get_latest_finalized_climbers,
    get_rank_evolution,
    sync_finalized_evolution_history,
)
from tests.conftest import run

# Tout ce qui alimente le classement général : on repart d'une base vierge
# le temps du test pour que les flèches ne dépendent que de nos fixtures.
_ISOLATED = (
    "sporting_day_rank_evolutions", "scores", "predictions", "matches",
    "pre_tournament_scores",
)
_RESTORE_ORDER = (
    "matches", "predictions", "scores", "pre_tournament_scores",
    "sporting_day_rank_evolutions",
)


@contextlib.contextmanager
def isolated_match_state():
    async def _backup():
        async with get_db() as db:
            await db.execute("PRAGMA foreign_keys=OFF")
            data = {}
            for table in _ISOLATED:
                rows = await db.execute(f"SELECT * FROM {table}")
                data[table] = [dict(r) for r in await rows.fetchall()]
                await db.execute(f"DELETE FROM {table}")
            await db.commit()
            return data

    async def _restore(data):
        async with get_db() as db:
            await db.execute("PRAGMA foreign_keys=OFF")
            for table in _ISOLATED:
                await db.execute(f"DELETE FROM {table}")
            for table in _RESTORE_ORDER:
                for row in data[table]:
                    cols = list(row.keys())
                    ph = ",".join("?" for _ in cols)
                    await db.execute(
                        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})",
                        [row[c] for c in cols],
                    )
            await db.commit()

    backup = run(_backup())
    try:
        yield
    finally:
        run(_restore(backup))


def _make_participant(name):
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                (name, f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _participant_token(participant_id):
    async def _query():
        async with get_db() as db:
            row = await db.execute(
                "SELECT token FROM participants WHERE id=?", (participant_id,)
            )
            return (await row.fetchone())["token"]

    return run(_query())


def _evolution_header(html):
    start = html.index('class="evo-ref')
    return html[start:html.index("</div>", start)]



def _make_match(number, match_date, kickoff="18:00", encoded=True,
                team1="France", team2="Brésil"):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight, result, score_team1, score_team2)
                   VALUES (?, 'group', ?, ?, ?, ?, 1, ?, ?, ?)""",
                (number, match_date, kickoff, team1, team2,
                 "team1" if encoded else None,
                 1 if encoded else None, 0 if encoded else None),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _award(pid, mid, points):
    async def _create():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, mid, points),
            )
            await db.commit()

    run(_create())


def _evolution():
    async def _q():
        async with get_db() as db:
            return await get_rank_evolution(db)

    return run(_q())


def _sync_history(from_day=None):
    async def _q():
        async with get_db() as db:
            await sync_finalized_evolution_history(db, from_day=from_day)
            await db.commit()
            return await get_latest_finalized_climbers(db)

    return run(_q())


def test_evolution_reflects_last_match_day_only(client):
    with isolated_match_state():
        a = _make_participant("Piste Alpha")
        b = _make_participant("Piste Beta")
        m_day1 = _make_match(970001, "2035-01-01")
        m_day2 = _make_match(970002, "2035-01-02")

        # Jour 1 : B mène (10 vs 2) → baseline avant jour 2 : B 1er, A 2e.
        _award(a, m_day1, 2)
        _award(b, m_day1, 10)
        # Jour 2 : A explose (12), B rien → total A 14, B 10 → A passe 1er.
        _award(a, m_day2, 12)
        _award(b, m_day2, 0)

        evo = _evolution()
        assert evo["day"] == "2035-01-02"
        assert evo["deltas"][a] == 1
        assert evo["deltas"][b] == -1


def test_evolution_uses_zero_or_pre_tournament_baseline_on_first_day(client):
    with isolated_match_state():
        a = _make_participant("Solo Alpha")
        b = _make_participant("Solo Beta")
        m = _make_match(970003, "2035-02-01")
        _award(a, m, 4)
        _award(b, m, 0)

        evo = _evolution()
        assert evo["day"] == "2035-02-01"
        assert evo["status"] == "finalized"
        assert evo["deltas"][a] == 0
        assert evo["deltas"][b] == -1


def test_evolution_independent_of_encoding_order(client):
    """Ne dépend que des dates de kickoff, pas de l'ordre d'insertion."""
    with isolated_match_state():
        a = _make_participant("Ordre Alpha")
        b = _make_participant("Ordre Beta")
        # Insertion du jour 2 AVANT le jour 1 : sans effet sur le résultat.
        m_day2 = _make_match(970005, "2035-03-02")
        m_day1 = _make_match(970004, "2035-03-01")
        _award(a, m_day1, 5)
        _award(b, m_day1, 5)   # ex æquo après jour 1
        _award(a, m_day2, 6)   # A creuse l'écart le jour 2
        _award(b, m_day2, 0)

        evo = _evolution()
        assert evo["day"] == "2035-03-02"
        # A était ex æquo avec B (rang 1) puis passe seul 1er : A monte, B stagne/descend.
        assert evo["deltas"][a] >= 0
        assert evo["deltas"][b] <= 0
        assert evo["deltas"][a] - evo["deltas"][b] >= 1


def test_overnight_matches_share_one_live_sporting_day(client):
    with isolated_match_state():
        a = _make_participant("Nuit Alpha")
        b = _make_participant("Nuit Beta")
        prior = _make_match(970010, "2035-03-31", "18:00")
        evening = _make_match(970011, "2035-04-01", "19:00")   # 21 h Bruxelles
        overnight = _make_match(970012, "2035-04-02", "02:00") # 4 h Bruxelles
        _award(a, prior, 0); _award(b, prior, 8)
        _award(a, evening, 6); _award(b, evening, 0)
        _award(a, overnight, 6); _award(b, overnight, 0)

        evo = _evolution()
        assert evo["day"] == "2035-04-01"
        assert evo["match_count"] == evo["encoded_count"] == 2
        assert evo["status"] == "finalized"
        assert evo["day_points"][a] == 12
        assert evo["deltas"][a] == 1


def test_climber_stays_on_last_finalized_day_while_next_day_is_live(client):
    with isolated_match_state():
        a = _make_participant("Fusée Alpha")
        b = _make_participant("Fusée Beta")
        c = _make_participant("Fusée Gamma")
        prior = _make_match(970020, "2035-04-30")
        finished = _make_match(970021, "2035-05-01")
        _award(a, prior, 0); _award(b, prior, 8); _award(c, prior, 6)
        _award(a, finished, 16); _award(b, finished, 0); _award(c, finished, 0)

        title = _sync_history()
        assert title["day"] == "2035-05-01"
        assert [r["participant_id"] for r in title["climbers"]] == [a]
        assert title["delta"] == 2

        live = _make_match(970022, "2035-05-02", "18:00")
        pending = _make_match(970023, "2035-05-03", "02:00", encoded=False)
        _award(a, live, 0); _award(b, live, 10); _award(c, live, 0)
        evo = _evolution()
        assert evo["day"] == "2035-05-02"
        assert evo["status"] == "in_progress"
        assert evo["encoded_count"] == 1 and evo["match_count"] == 2
        assert _sync_history()["day"] == "2035-05-01"

        html = client.get(f"/p/{_participant_token(a)}/classement").text
        evolution_header = _evolution_header(html)
        assert 'class="evo-ref is-progress"' in html
        assert 'class="evo-ref-status"' in html
        assert "Actualisé après" in evolution_header
        assert "France" in evolution_header and "Brésil" in evolution_header
        assert "1–0" in evolution_header
        assert "résultats encodés" not in evolution_header
        assert "evo-ref-day" not in evolution_header


        async def _finish():
            async with get_db() as db:
                await db.execute(
                    "UPDATE matches SET result='team1', score_team1=1, score_team2=0 WHERE id=?",
                    (pending,),
                )
                await db.commit()
        run(_finish())
        _award(a, pending, 0); _award(b, pending, 6); _award(c, pending, 0)
        new_title = _sync_history("2035-05-02")
        assert new_title["day"] == "2035-05-02"
        # Le nouveau jour est bien le dernier finalisé ; aucun titre si le
        # meilleur gain reste sous le seuil historique de deux places.
        assert new_title["climbers"] == []


def test_climber_banner_lists_its_sporting_day_matches(client):
    with isolated_match_state():
        a = _make_participant("Fusée Liste")
        b = _make_participant("Liste Beta")
        c = _make_participant("Liste Gamma")
        prior = _make_match(970024, "2035-05-31")
        evening = _make_match(
            970025, "2035-06-01", "18:00",
            team1="Canada", team2="Qatar",
        )
        overnight = _make_match(
            970026, "2035-06-02", "02:00",
            team1="Japon", team2="Maroc",
        )

        async def _make_overnight_knockout():
            async with get_db() as db:
                await db.execute(
                    """UPDATE matches
                       SET phase='round_of_16', result='draw', score_team1=2,
                           score_team2=2, qualifier_winner='team2'
                       WHERE id=?""",
                    (overnight,),
                )
                await db.commit()

        run(_make_overnight_knockout())
        _award(a, prior, 0); _award(b, prior, 8); _award(c, prior, 6)
        _award(a, evening, 8); _award(b, evening, 0); _award(c, evening, 0)
        _award(a, overnight, 8); _award(b, overnight, 0); _award(c, overnight, 0)
        title = _sync_history()
        assert title["day"] == "2035-06-01"

        html = client.get(f"/p/{_participant_token(a)}/classement").text
        assert "Grimpeur · dernière journée finalisée" in html
        assert "Fusée Liste" in html
        assert "vendredi 1 juin" in html
        assert 'class="chip up ch-delta">▲ 2 places</span>' in html
        assert 'data-tooltip-content="climber-day-tooltip"' in html
        assert '<template id="climber-day-tooltip">' in html
        assert "Pourquoi ce Grimpeur ?" in html
        assert "Ce titre récompense la meilleure progression au classement" in html
        assert "Journée sportive du vendredi 1 juin" in html
        assert "Du vendredi 1 juin à 9 h au samedi 2 juin à 8 h 59" in html
        assert "2 matchs pris en compte" in html
        assert "Canada" in html and "Qatar" in html and "1–0" in html
        assert "Japon" in html and "Maroc" in html and "2–2" in html
        assert "Maroc qualifié" in html


def test_climber_card_groups_tied_names(client):
    with isolated_match_state():
        first = _make_participant("Alpha Longnom")
        second = _make_participant("Beta Longnom")
        _make_match(970027, "2035-06-18")

        async def _seed_tie():
            async with get_db() as db:
                for participant_id in (first, second):
                    await db.execute(
                        """INSERT INTO sporting_day_rank_evolutions
                           (sporting_day, participant_id, points_before, day_points,
                            points_after, rank_before, rank_after, delta, is_climber)
                           VALUES ('2035-06-18', ?, 0, 10, 10, 4, 1, 3, 1)""",
                        (participant_id,),
                    )
                await db.commit()

        run(_seed_tie())
        html = client.get(f"/p/{_participant_token(first)}/classement").text

        assert "Grimpeurs · dernière journée finalisée" in html
        assert "Alpha Longnom · Beta Longnom" in html
        assert 'class="chip up ch-delta">▲ 3 places</span>' in html
        assert "Pourquoi ces Grimpeurs ?" in html
        assert "En cas d’égalité, il est partagé." in html


def test_ranking_live_header_and_rich_tooltip_css_contract():
    source = Path("app/static/css/resa.css").read_text()

    assert ".evo-ref.is-progress" in source
    assert "flex-direction: column;" in source
    assert ".evo-ref-last" in source
    assert ".floating-tooltip.rich" in source
    assert ".climber-tooltip-match" in source


def test_ranking_trophy_badges_keep_a_light_paper_palette():
    source = Path("app/static/css/resa.css").read_text()
    badge_css = source.split("/* Mini badge du classement", 1)[1].split(
        "@keyframes badge-pop", 1
    )[0]

    assert "#0a0a0d" not in badge_css
    assert "#fffdf9" in badge_css
    assert "radial-gradient" in badge_css
    assert "--mb-sh" in badge_css


def test_tooltip_player_keeps_plain_text_and_supports_trusted_templates():
    source = Path("app/static/js/resa.js").read_text()

    assert "trigger.getAttribute('data-tip')" in source
    assert "bubble.textContent = text" in source
    assert "trigger.getAttribute('data-tooltip-content')" in source
    assert "source.content.cloneNode(true)" in source


def test_live_header_uses_latest_encoded_kickoff_and_qualifier(client):
    with isolated_match_state():
        a = _make_participant("Live Alpha")
        b = _make_participant("Live Beta")
        early = _make_match(
            970028, "2035-07-01", "18:00",
            team1="Canada", team2="Qatar",
        )
        latest = _make_match(
            970029, "2035-07-01", "20:00",
            team1="États-Unis", team2="Australie",
        )
        _make_match(970030, "2035-07-02", "02:00", encoded=False)

        async def _make_latest_knockout():
            async with get_db() as db:
                await db.execute(
                    """UPDATE matches
                       SET phase='round_of_16', result='draw', score_team1=1,
                           score_team2=1, qualifier_winner='team2'
                       WHERE id=?""",
                    (latest,),
                )
                await db.commit()

        run(_make_latest_knockout())
        _award(a, early, 2); _award(b, early, 0)
        _award(a, latest, 0); _award(b, latest, 4)

        html = client.get(f"/p/{_participant_token(a)}/classement").text
        evolution_header = _evolution_header(html)

        assert "Actualisé après" in evolution_header
        assert "États-Unis" in evolution_header
        assert "1–1" in evolution_header
        assert "Australie" in evolution_header
        assert "Australie qualifié" in evolution_header
        assert "Canada" not in evolution_header
        assert "résultats encodés" not in evolution_header


def test_live_header_has_safe_fallback_without_match_details(
    client, monkeypatch,
):
    with isolated_match_state():
        participant = _make_participant("Fallback Alpha")
        opponent = _make_participant("Fallback Beta")
        encoded = _make_match(970031, "2035-08-01", "18:00")
        _make_match(970032, "2035-08-01", "20:00", encoded=False)
        _award(participant, encoded, 2); _award(opponent, encoded, 0)

        async def _no_details(db, day):
            return []

        monkeypatch.setattr(page_routes, "_sporting_day_match_details", _no_details)
        html = client.get(f"/p/{_participant_token(participant)}/classement").text
        evolution_header = _evolution_header(html)

        assert "Évolution en cours" in evolution_header
        assert "Classement actualisé avec les résultats encodés" in evolution_header


def test_late_correction_replaces_persisted_climber(client):
    with isolated_match_state():
        a = _make_participant("Correction Alpha")
        b = _make_participant("Correction Beta")
        c = _make_participant("Correction Gamma")
        d = _make_participant("Correction Delta")
        prior = _make_match(970030, "2035-05-30")
        corrected = _make_match(970031, "2035-05-31")
        for pid, points in ((a, 0), (b, 30), (c, 20), (d, 10)):
            _award(pid, prior, points)
        for pid, points in ((a, 40), (b, 0), (c, 0), (d, 0)):
            _award(pid, corrected, points)

        first = _sync_history()
        assert [r["participant_id"] for r in first["climbers"]] == [a]

        async def _correct():
            async with get_db() as db:
                await db.execute(
                    "UPDATE scores SET points=0 WHERE participant_id=? AND match_id=?",
                    (a, corrected),
                )
                await db.execute(
                    "UPDATE scores SET points=30 WHERE participant_id=? AND match_id=?",
                    (d, corrected),
                )
                await sync_finalized_evolution_history(db, from_day="2035-05-31")
                await db.commit()
                return await get_latest_finalized_climbers(db)

        updated = run(_correct())
        assert [r["participant_id"] for r in updated["climbers"]] == [d]
        assert updated["delta"] == 2
