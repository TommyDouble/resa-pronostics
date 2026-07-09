"""Les stats de profil ne doivent pas révéler les pronos des matchs non verrouillés."""
import contextlib
from html import unescape
import uuid

from app.database import get_db
from app.routers.pages import _build_profile
from tests.conftest import run


def _make_participant():
    token = str(uuid.uuid4())

    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                "INSERT INTO participants (name, email, token, is_confirmed) VALUES (?,?,?,1)",
                ("Profil Stats", f"{token}@test.local", token),
            )
            await db.commit()
            return cursor.lastrowid, token

    return run(_create())


def _make_match(number, match_date, team1="France", team2="Brésil", result=None,
                score_team1=None, score_team2=None):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                                        team1_name, team2_name, weight,
                                        score_team1, score_team2, result)
                   VALUES (?, 'group', ?, '12:00', ?, ?, 1, ?, ?, ?)""",
                (number, match_date, team1, team2, score_team1, score_team2, result),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _predict(pid, mid, prediction, s1, s2):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                                            exact_score_team1, exact_score_team2)
                   VALUES (?,?,?,?,?)""",
                (pid, mid, prediction, s1, s2),
            )
            await db.commit()

    run(_create())


def _score_match(pid, mid, points):
    async def _create():
        async with get_db() as db:
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, mid, points),
            )
            await db.commit()

    run(_create())


def _make_bonus_question(question_text="Journal bonus — Question de test"):
    async def _create():
        async with get_db() as db:
            cursor = await db.execute(
                """INSERT INTO bonus_questions
                   (question_text, phase, answer_type, options, points_value, deadline)
                   VALUES (?, 'group', 'choice', '["Oui","Non"]', 5, '2040-06-01T12:00:00')""",
                (question_text,),
            )
            await db.commit()
            return cursor.lastrowid

    return run(_create())


def _score_bonus(pid, question_id, points, calculated_at="2040-06-12T12:00:00"):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO scores
                   (participant_id, bonus_question_id, points, calculated_at)
                   VALUES (?,?,?,?)""",
                (pid, question_id, points, calculated_at),
            )
            await db.commit()

    run(_create())


def _score_pre_tournament(pid, key, points, calculated_at="2040-06-13T12:00:00"):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO pre_tournament_scores
                   (participant_id, question_key, points, calculated_at)
                   VALUES (?,?,?,?)""",
                (pid, key, points, calculated_at),
            )
            await db.commit()

    run(_create())


def _point_event(pid, source, source_key, delta, occurred_at):
    async def _create():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO scoring_point_events
                   (source, source_key, participant_id, delta, occurred_at)
                   VALUES (?,?,?,?,?)""",
                (source, str(source_key), pid, delta, occurred_at),
            )
            await db.commit()

    run(_create())


@contextlib.contextmanager
def _isolated_point_events(pid):
    try:
        yield
    finally:
        async def _cleanup():
            async with get_db() as db:
                await db.execute(
                    "DELETE FROM scoring_point_events WHERE participant_id=?",
                    (pid,),
                )
                await db.commit()

        run(_cleanup())


def _profile(pid):
    async def _q():
        async with get_db() as db:
            return await _build_profile(pid, db)

    return run(_q())


def _cabinet_fragment(html):
    start = html.index("<!-- Cabinet à trophées -->")
    end = html.index("<!-- Stats fun -->", start)
    return html[start:end]


def _fun_stat(profile, label):
    return next((s for s in profile["fun_stats"] if s["label"] == label), None)


def test_draw_attempts_ignore_unlocked_matches(client):
    pid, _ = _make_participant()
    future = _make_match(930001, "2099-01-01")
    _predict(pid, future, "draw", 1, 1)

    profile = _profile(pid)
    assert _fun_stat(profile, "Matchs nuls tentés / réussis") is None

    past = _make_match(930002, "2000-01-01")
    _predict(pid, past, "draw", 0, 0)

    profile = _profile(pid)
    stat = _fun_stat(profile, "Matchs nuls tentés / réussis")
    assert stat is not None
    assert stat["value"].startswith("1 /")  # seul le match verrouillé compte


def test_favorite_pick_ignores_unlocked_matches(client):
    pid, _ = _make_participant()
    future = _make_match(930003, "2099-01-01", "Argentine", "Portugal")
    _predict(pid, future, "team1", 2, 0)

    profile = _profile(pid)
    assert _fun_stat(profile, "Équipe la plus jouée gagnante") is None

    past = _make_match(930004, "2000-01-01", "Argentine", "Portugal")
    _predict(pid, past, "team1", 1, 0)

    profile = _profile(pid)
    stat = _fun_stat(profile, "Équipe la plus jouée gagnante")
    assert stat is not None
    assert "Argentine" in stat["value"]
    assert "(1×)" in stat["value"]  # le match futur n'est pas compté


def test_home_mini_ranking_links_to_profiles(client):
    pid, token = _make_participant()
    html = client.get(f"/p/{token}").text
    assert f'href="/p/{token}/profil' in html
    assert 'class="rrow' in html


def test_limited_profile_hides_trophy_cabinet_for_other_viewer(client):
    target_id, _ = _make_participant()
    _, viewer_token = _make_participant()

    async def _limit():
        async with get_db() as db:
            await db.execute(
                "UPDATE participants SET profile_visibility='limited' WHERE id=?",
                (target_id,),
            )
            await db.commit()

    run(_limit())
    html = client.get(f"/p/{viewer_token}/profil/{target_id}").text
    assert "Profil limité aux stats publiques" in html
    assert "Cabinet à trophées" not in html


def _refresh():
    from app.trophies import refresh_trophy_awards

    async def _c():
        async with get_db() as db:
            await refresh_trophy_awards(db)
            await db.commit()
    run(_c())


def _unlocked_keys(pid):
    async def _c():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT DISTINCT trophy_key FROM trophy_awards WHERE participant_id=?", (pid,)
            )
            return {r["trophy_key"] for r in await rows.fetchall()}
    return run(_c())


def test_trophy_cabinet_renders_noto_assets_without_native_emoji(client):
    """Refonte premium : cabinet rendu avec des assets Noto SVG locaux,
    sans sprite PNG, sans paliers, et SANS emoji natif comme rendu principal."""
    from app.trophies import TROPHIES
    pid, token = _make_participant()
    html = client.get(f"/p/{token}/profil").text
    cabinet = _cabinet_fragment(html)
    # Plus de sprite PNG ni de paliers/médailles.
    assert "trophy-symbol-sprite" not in html
    assert "/static/img/trophy-silhouettes/" not in html
    assert "t-medal--" not in cabinet  # plus de badge de palier (ancien système)
    assert 'class="t-pips"' not in cabinet and 'class="t-bar"' not in cabinet
    # Une tuile par trophée du catalogue.
    assert cabinet.count('class="trophy ') == len(TROPHIES)
    # Icônes servies en assets Noto locaux (pas de CDN, pas d'emoji natif principal).
    assert 'class="t-noto"' in cabinet
    assert "/static/img/trophies/noto/" in cabinet
    assert "🥄" not in cabinet and "🔮" not in cabinet  # plus d'emoji système
    # Secret verrouillé : cadenas élégant + libellé masqué, pas d'emoji 🔒.
    assert 'class="t-lock"' in cabinet and "Trophée secret" in cabinet
    assert "🔒" not in cabinet
    # Hooks de rareté propagés sur les tuiles.
    assert "rar-legendary" in cabinet and "rar-anti" in cabinet
    # Progression toujours présente, dernier trophée absent sans attribution.
    assert 'class="cab-progress"' in cabinet
    assert 'aria-valuenow="0"' in cabinet
    assert 'class="cab-latest"' not in cabinet


def test_trophy_cabinet_shows_latest_business_day_and_repeat_count(client):
    pid, token = _make_participant()

    async def _seed():
        async with get_db() as db:
            # L'ordre métier suit sporting_day, même si l'écriture la plus récente
            # en base concerne une journée plus ancienne (cas d'un backfill).
            await db.execute(
                """INSERT INTO trophy_awards
                   (participant_id, trophy_key, detail, sporting_day, awarded_at)
                   VALUES (?, 'journee_parfaite', '2044-07-08', '2044-07-08', '2044-07-08 18:00:00')""",
                (pid,),
            )
            await db.execute(
                """INSERT INTO trophy_awards
                   (participant_id, trophy_key, detail, sporting_day, awarded_at)
                   VALUES (?, 'grimpeur', '2044-07-07', '2044-07-07', '2099-01-01 12:00:00')""",
                (pid,),
            )
            await db.execute(
                """INSERT INTO trophy_awards
                   (participant_id, trophy_key, detail, sporting_day, awarded_at)
                   VALUES (?, 'journee_parfaite', '2044-07-09', '2044-07-09', '2044-07-09 18:00:00')""",
                (pid,),
            )
            await db.commit()

    run(_seed())
    profile = _profile(pid)
    assert profile["latest_trophy"]["key"] == "journee_parfaite"
    assert profile["latest_trophy"]["count"] == 2
    assert profile["latest_trophy"]["detail_label"] == "Journée du samedi 9 juillet"

    cabinet = _cabinet_fragment(client.get(f"/p/{token}/profil").text)
    assert 'class="cab-latest"' in cabinet
    assert "La Journée Parfaite" in cabinet
    assert "×2" in cabinet
    assert 'aria-valuenow="2"' in cabinet


def test_trophy_cabinet_tooltip_keeps_dated_history(client):
    pid, token = _make_participant()

    async def _seed():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO trophy_awards
                   (participant_id, trophy_key, detail, sporting_day)
                   VALUES (?, 'grimpeur', '2044-07-07', '2044-07-07')""",
                (pid,),
            )
            await db.commit()

    run(_seed())
    cabinet = _cabinet_fragment(client.get(f"/p/{token}/profil").text)
    assert "Historique : Journée du" in cabinet
    assert "7 juillet." in cabinet


def test_own_profile_point_journal_replaces_last_matches_and_includes_zero_sources(client, participant):
    pid = participant["id"]
    token = participant["token"]
    base_number = 9800000 + pid * 10
    won = _make_match(base_number, "2040-06-10", "France", "Brésil",
                      result="team1", score_team1=2, score_team2=1)
    missed = _make_match(base_number + 1, "2040-06-11", "Espagne", "Italie",
                         result="team2", score_team1=0, score_team2=1)
    _predict(pid, won, "team1", 2, 1)
    _predict(pid, missed, "team1", 1, 0)
    _score_match(pid, won, 4)

    bonus_id = _make_bonus_question("Journal bonus — Score bonus ?")
    _score_bonus(pid, bonus_id, 0)
    _score_pre_tournament(pid, "winner", 0)

    html = unescape(client.get(f"/p/{token}/profil").text)
    assert 'data-point-journal' in html
    assert "Mes derniers points" in html
    assert "5 derniers matchs" not in html
    assert "France - Brésil" in html
    assert "Espagne - Italie" in html
    assert "Journal bonus" in html
    assert "Champion du Monde" in html
    assert "0 pt" in html


def test_point_journal_shows_bonus_correction_delta(client, participant):
    pid = participant["id"]
    token = participant["token"]
    with _isolated_point_events(pid):
        bonus_id = _make_bonus_question("Correction bonus — Question corrigée ?")
        _score_bonus(pid, bonus_id, 2)
        _point_event(pid, "bonus", bonus_id, 5, "2040-06-10T12:00:00")
        _point_event(pid, "bonus", bonus_id, -3, "2040-06-11T12:00:00")

        html = unescape(client.get(f"/p/{token}/profil").text)
        assert "Correction bonus" in html
        assert "-3 pts" in html


def test_point_journal_is_private_to_own_profile(client):
    target_id, _ = _make_participant()
    _, viewer_token = _make_participant()
    mid = _make_match(9810000 + target_id, "2040-06-10", result="team1",
                      score_team1=1, score_team2=0)
    _predict(target_id, mid, "team1", 1, 0)
    _score_match(target_id, mid, 4)

    html = client.get(f"/p/{viewer_token}/profil/{target_id}").text
    assert "Mes derniers points" not in html
    assert "5 derniers matchs" in html


def test_journee_parfaite_requires_all_day_matches_predicted(client):
    pid, _ = _make_participant()
    base_number = 9900000 + pid * 10
    mids = [
        _make_match(base_number + i, "2044-07-07", result="team1", score_team1=1, score_team2=0)
        for i in range(4)
    ]
    for mid in mids[:3]:
        _predict(pid, mid, "team1", 1, 0)
    _refresh()
    assert "journee_parfaite" not in _unlocked_keys(pid)

    _predict(pid, mids[3], "team1", 1, 0)
    _refresh()
    assert "journee_parfaite" in _unlocked_keys(pid)
