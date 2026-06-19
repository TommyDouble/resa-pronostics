"""W1.1 — Reveal du jour v2 : journée sportive, gate d'encodage, fenêtre, /api/reveal/seen."""
from datetime import timedelta

from app.database import get_db, _migrate_reveal_sporting_day
import app.routers.pages as pages
from app.routers.pages import _register_daily_connection, _reveal_window_data
from app.timeutils import now_utc, sporting_day
from tests.conftest import run


def _reset_matches():
    """Isole le scénario : la fenêtre du reveal cible les journées finalisées,
    donc on repart d'une table matches vide (cascade scores/predictions)."""
    async def _c():
        async with get_db() as db:
            await db.execute("DELETE FROM matches")
            await db.commit()
    run(_c())


def _yesterday_match(number, hour, result=None, s1=None, s2=None):
    """Match daté d'hier (UTC) à l'heure donnée → déjà joué (kicked off)."""
    d = (now_utc() - timedelta(days=1)).strftime("%Y-%m-%d")

    async def _c():
        async with get_db() as db:
            cur = await db.execute(
                """INSERT INTO matches (match_number, phase, group_name, match_date, kickoff_time,
                                        team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?, 'group', 'Groupe R', ?, ?, 'France', 'Brésil', 1, ?, ?, ?)""",
                (number, d, f"{hour:02d}:00:00", s1, s2, result),
            )
            await db.commit()
            return cur.lastrowid

    return run(_c())


def _encode(mid, s1, s2, result):
    async def _c():
        async with get_db() as db:
            await db.execute(
                "UPDATE matches SET score_team1=?, score_team2=?, result=? WHERE id=?",
                (s1, s2, result, mid),
            )
            await db.commit()
    run(_c())


def _pred_score(pid, mid, es1, es2, points, prediction="team1"):
    async def _c():
        async with get_db() as db:
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                                            exact_score_team1, exact_score_team2)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(participant_id, match_id) DO UPDATE SET
                     prediction=excluded.prediction,
                     exact_score_team1=excluded.exact_score_team1,
                     exact_score_team2=excluded.exact_score_team2""",
                (pid, mid, prediction, es1, es2),
            )
            await db.execute(
                "INSERT INTO scores (participant_id, match_id, points) VALUES (?,?,?)",
                (pid, mid, points),
            )
            await db.commit()
    run(_c())


def _last_revealed(pid):
    async def _g():
        async with get_db() as db:
            row = await db.execute(
                "SELECT last_revealed_date FROM participants WHERE id=?", (pid,)
            )
            return (await row.fetchone())["last_revealed_date"]
    return run(_g())


def _window(pid):
    async def _g():
        async with get_db() as db:
            return await _reveal_window_data(db, pid)
    return run(_g())


def _connection_state(pid):
    async def _g():
        async with get_db() as db:
            row = await (await db.execute(
                """SELECT last_connected_sporting_day, reveal_connection_baseline_day,
                          last_revealed_date FROM participants WHERE id=?""",
                (pid,),
            )).fetchone()
            return dict(row)
    return run(_g())


def _explicit_result_match(number, day):
    async def _c():
        async with get_db() as db:
            cur = await db.execute(
                """INSERT INTO matches
                   (match_number, phase, match_date, kickoff_time, team1_name, team2_name,
                    weight, score_team1, score_team2, result)
                   VALUES (?, 'group', ?, '12:00:00', 'France', 'Brésil', 1, 1, 0, 'team1')""",
                (number, day),
            )
            await db.commit()
            return cur.lastrowid
    return run(_c())


def test_sporting_day_groups_overnight():
    # Un match de soirée et un match d'après-minuit partagent la même journée sportive.
    evening = {"match_date": "2026-06-14", "kickoff_time": "19:00:00"}  # ~21h Bruxelles
    overnight = {"match_date": "2026-06-15", "kickoff_time": "02:00:00"}  # ~04h Bruxelles
    assert sporting_day(evening) == sporting_day(overnight)


def test_reveal_waits_for_full_encoding_then_available(client, participant):
    _reset_matches()
    a = _yesterday_match(961001, 20, result="team1", s1=2, s2=0)  # encodé
    b = _yesterday_match(961002, 22)                              # joué mais PAS encodé
    _pred_score(participant["id"], a, 2, 0, 5)

    # Un match joué non encodé dans le périmètre → on attend (pas de reveal).
    assert _window(participant["id"]) is None

    _encode(b, 1, 1, "draw")  # on encode le dernier match de la nuit
    win = _window(participant["id"])
    assert win is not None
    assert win["match_count"] == 2  # les 2 matchs de la nuit, ensemble


def test_reveal_page_renders_sequence_and_evolution(client, participant):
    _reset_matches()
    a = _yesterday_match(961010, 20, result="team1", s1=2, s2=0)
    b = _yesterday_match(961011, 22, result="draw", s1=1, s2=1)
    _pred_score(participant["id"], a, 2, 0, 5)   # exact
    _pred_score(participant["id"], b, 0, 2, 1, prediction="team2")

    win = _window(participant["id"])
    assert win["total_points"] == 6
    assert win["evolution"] and win["evolution"]["after"]

    html = client.get(f"/p/{participant['token']}/reveal").text
    assert "data-reveal" in html
    assert html.count("data-reveal-match") == 2
    assert "data-reveal-final" in html
    assert "+6 pts" in html


def test_reveal_seen_advances_pointer(client, participant):
    _reset_matches()
    a = _yesterday_match(961020, 20, result="team1", s1=2, s2=0)
    _pred_score(participant["id"], a, 2, 0, 5)
    assert _window(participant["id"]) is not None

    res = client.post(f"/api/reveal/seen?token={participant['token']}")
    assert res.status_code == 200
    # Le repère a avancé à la journée sportive du lot → plus rien à révéler.
    assert _last_revealed(participant["id"]) is not None
    assert _window(participant["id"]) is None


def test_home_entry_and_story_promo(client, participant):
    _reset_matches()
    a = _yesterday_match(961030, 20, result="team1", s1=2, s2=0)
    _pred_score(participant["id"], a, 2, 0, 5)

    html = client.get(f"/p/{participant['token']}").text
    assert "reveal-entry" in html
    assert "Le Reveal du jour est prêt" in html
    assert "1 match" in html
    assert "re-go" not in html
    # La story promo (parcours multi-écrans) reste rendue indépendamment.
    assert "data-story-feature" in html
    assert "rp-demo" in html


def test_home_recap_mirrors_reveal(client, participant):
    """Le CTA fusionné reflète la fenêtre Reveal puis disparaît après lecture."""
    _reset_matches()
    a = _yesterday_match(961040, 20, result="team1", s1=2, s2=0)
    _pred_score(participant["id"], a, 2, 0, 5)  # score exact
    win = _window(participant["id"])

    html = client.get(f"/p/{participant['token']}").text
    assert html.count("data-home-reveal") == 1
    assert "yesterday-card" not in html
    assert f"+{win['total_points']} pts" in html  # mêmes points que le Reveal

    # Une fois le Reveal vu, l'unique bloc fusionné disparaît.
    client.post(f"/api/reveal/seen?token={participant['token']}")
    html2 = client.get(f"/p/{participant['token']}").text
    assert "data-home-reveal" not in html2


def test_migration_reveal_sporting_day_rolls_back_one_day(participant):
    """Migration one-shot : recule last_revealed_date d'un jour, une seule fois.

    Une valeur héritée (date calendrier) masquait la nuit sportive incluant les
    matchs 0h–9h ; on la recule d'un cran sans jamais reculer deux fois."""
    async def _c():
        async with get_db() as db:
            await db.execute("DELETE FROM app_settings WHERE key='migr_reveal_sporting_day_v1'")
            await db.execute(
                "UPDATE participants SET last_revealed_date='2026-06-13' WHERE id=?",
                (participant["id"],),
            )
            await db.commit()

            async def revealed():
                row = await (await db.execute(
                    "SELECT last_revealed_date FROM participants WHERE id=?", (participant["id"],)
                )).fetchone()
                return row["last_revealed_date"]

            await _migrate_reveal_sporting_day(db); await db.commit()
            first = await revealed()
            await _migrate_reveal_sporting_day(db); await db.commit()  # 2e passage
            second = await revealed()
            return first, second

    first, second = run(_c())
    assert first == "2026-06-12"   # reculé d'un jour
    assert second == "2026-06-12"  # idempotent : pas de second recul


def test_connection_baseline_moves_only_on_first_home_of_sporting_day(
    client, participant, monkeypatch
):
    async def _seed():
        async with get_db() as db:
            await db.execute(
                """UPDATE participants
                   SET last_connected_sporting_day='2035-01-01',
                       reveal_connection_baseline_day='2034-12-31',
                       last_revealed_date='2034-12-30'
                   WHERE id=?""",
                (participant["id"],),
            )
            await db.commit()
    run(_seed())
    monkeypatch.setattr(pages, "current_sporting_day", lambda: "2035-01-02")

    async def _register():
        async with get_db() as db:
            return await _register_daily_connection(db, participant["id"])

    assert run(_register()) == "2035-01-01"
    assert run(_register()) == "2035-01-01"
    state = _connection_state(participant["id"])
    assert state["last_connected_sporting_day"] == "2035-01-02"
    assert state["reveal_connection_baseline_day"] == "2035-01-01"

    monkeypatch.setattr(pages, "current_sporting_day", lambda: "2035-01-03")
    assert run(_register()) == "2035-01-02"


def test_reveal_uses_connection_baseline_but_guarantees_latest_finalized_day(
    client, participant
):
    _reset_matches()
    mids = [
        _explicit_result_match(962001, "2035-02-01"),
        _explicit_result_match(962002, "2035-02-02"),
        _explicit_result_match(962003, "2035-02-03"),
    ]
    for mid in mids:
        _pred_score(participant["id"], mid, 1, 0, 2)

    async def _set(baseline, seen="2035-01-31"):
        async with get_db() as db:
            await db.execute(
                """UPDATE participants
                   SET reveal_connection_baseline_day=?, last_revealed_date=?
                   WHERE id=?""",
                (baseline, seen, participant["id"]),
            )
            await db.commit()

    run(_set("2035-01-31"))
    catchup = _window(participant["id"])
    assert catchup["day_count"] == 3
    assert catchup["is_catchup"] is True
    assert catchup["match_count"] == 3

    # Connexion quotidienne : seule la dernière journée reste dans la fenêtre.
    run(_set("2035-02-03"))
    latest = _window(participant["id"])
    assert latest["day_count"] == 1
    assert latest["sporting_day"] == "2035-02-03"

    # Si une nouvelle journée se finalise pendant la lecture, le client ne
    # marque comme vu que le périmètre réellement affiché.
    run(_set("2035-02-02"))
    res = client.post(
        f"/api/reveal/seen?token={participant['token']}&sporting_day=2035-02-02"
    )
    assert res.status_code == 200
    assert _last_revealed(participant["id"]) == "2035-02-02"

    # Une fois réellement vue, elle disparaît.
    run(_set("2035-02-03", seen="2035-02-03"))
    assert _window(participant["id"]) is None
