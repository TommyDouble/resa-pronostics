"""W7 — Cabinet à trophées : logique pure app.trophies + série de connexions."""
from app.database import get_db
from app.routers.pages import _record_daily_visit
from app.timeutils import local_today
from app.trophies import evaluate, summarize, CATEGORIES
from tests.conftest import run


def _by_key(trophies):
    return {t["key"]: t for t in trophies}


def _empty_metrics():
    return {
        "match_count": 0, "present_streak": 0, "total_played": 0, "total_results": 0,
        "exact": 0, "bonus_points": 0, "near_miss": 0, "longest_streak": 0,
        "draw_correct": 0, "last_minute_count": 0, "perfect_day": False,
        "climber_count": 0,
    }


def test_evaluate_shape_and_categories():
    trophies = evaluate(_empty_metrics())
    assert len(trophies) == 12
    valid = {c[0] for c in CATEGORIES}
    assert all(t["category"] in valid for t in trophies)
    # Tout verrouillé sur des métriques vides.
    assert not any(t["unlocked"] for t in trophies)


def test_first_step_unlocks_immediately():
    m = _empty_metrics()
    m["match_count"] = 1
    assert _by_key(evaluate(m))["first_step"]["unlocked"] is True


def test_tiered_sniper_levels_and_progress():
    m = _empty_metrics()
    m["exact"] = 12
    sniper = _by_key(evaluate(m))["sniper"]
    assert sniper["unlocked"] and sniper["tier"] == "argent"   # 10 ≤ 12 < 20
    assert sniper["target"] == 20
    # progression entre le palier argent (10) et or (20) : 2/10
    assert 0.19 <= sniper["progress"] <= 0.21
    # palier maxi → plus de cible
    m["exact"] = 40
    assert _by_key(evaluate(m))["sniper"]["target"] is None


def test_tiered_always_shows_next_step():
    """Même débloqué, un trophée à paliers indique la marche suivante (médaille + reste)."""
    m = _empty_metrics()
    m["longest_streak"] = 4  # bronze (3) atteint, argent (5) en vue
    streak = _by_key(evaluate(m))["streak"]
    assert streak["unlocked"] and streak["tier"] == "bronze"
    assert streak["next_tier"] == "argent" and streak["next_medal"] == "argent"
    assert streak["target"] == 5 and streak["remaining"] == 1


def test_last_minute_tiered_levels():
    m = _empty_metrics()
    m["last_minute_count"] = 4
    late = _by_key(evaluate(m))["last_minute"]
    assert late["unlocked"] is False
    assert late["target"] == 5 and late["remaining"] == 1

    m["last_minute_count"] = 12
    late = _by_key(evaluate(m))["last_minute"]
    assert late["unlocked"] and late["tier"] == "argent"
    assert late["target"] == 20 and late["next_tier"] == "or"

    m["last_minute_count"] = 35
    late = _by_key(evaluate(m))["last_minute"]
    assert late["tier"] == "diamant" and late["target"] is None


def test_marathon_top_tier_closes_on_last_match():
    """Le palier DIAMANT de Marathonien = nombre total de matchs de la compétition."""
    m = _empty_metrics()
    m["total_matches"] = 104
    m["match_count"] = 90  # entre or (80) et diamant (104)
    marathon = _by_key(evaluate(m))["marathon"]
    assert marathon["target"] == 104 and marathon["next_tier"] == "diamant"
    # Tous les matchs pronostiqués → palier maxi (diamant), plus de cible.
    m["match_count"] = 104
    full = _by_key(evaluate(m))["marathon"]
    assert full["tier"] == "diamant" and full["target"] is None


def test_all_tiered_reach_diamant_and_chocolate_order():
    """Tous les trophées à paliers culminent en diamant ; l'ordre inclut chocolat."""
    from app.trophies import _MEDALS
    order = list(_MEDALS)
    assert order == ["chocolat", "bronze", "argent", "or", "diamant"]
    # Métriques très hautes → chaque trophée à paliers doit atteindre "diamant".
    m = {"match_count": 999, "total_matches": 104, "exact": 999, "present_streak": 999,
         "longest_streak": 999, "draw_correct": 999, "last_minute_count": 999,
         "total_played": 0, "total_results": 0, "bonus_points": 999, "near_miss": 0,
         "perfect_day": False, "climber_count": 999}
    for t in _by_key(evaluate(m)).values():
        if t["key"] in ("sniper", "present", "marathon", "streak", "draw_king", "last_minute", "bonus_king", "climber"):
            assert t["tier"] == "diamant", t["key"]


def test_climber_tiered_levels():
    m = _empty_metrics()
    c = _by_key(evaluate(m))["climber"]
    assert c["unlocked"] is False
    assert c["category"] == "adresse"
    assert c["target"] == 1 and c["remaining"] == 1

    m["climber_count"] = 1
    c = _by_key(evaluate(m))["climber"]
    assert c["unlocked"] and c["tier"] == "chocolat"
    assert c["target"] == 3 and c["next_tier"] == "bronze"

    m["climber_count"] = 5
    c = _by_key(evaluate(m))["climber"]
    assert c["tier"] == "argent" and c["target"] == 8

    m["climber_count"] = 12
    c = _by_key(evaluate(m))["climber"]
    assert c["tier"] == "diamant" and c["target"] is None


def test_secret_perfect_day_hidden_until_unlocked():
    m = _empty_metrics()
    pd = _by_key(evaluate(m))["perfect_day"]
    assert pd["secret"] is True and pd["unlocked"] is False
    m["perfect_day"] = True
    assert _by_key(evaluate(m))["perfect_day"]["unlocked"] is True


def test_summarize_nearest_is_highest_progress_locked():
    m = _empty_metrics()
    m["exact"] = 1         # sniper 1/2 (chocolat) = 0.5, le plus proche, encore verrouillé
    m["draw_correct"] = 1  # roi du nul 1/3 ≈ 0.33
    s = summarize(evaluate(m))
    assert s["unlocked_count"] == 0
    assert s["nearest"]["key"] == "sniper"
    # un secret verrouillé ne doit jamais être proposé comme "le plus proche"
    assert s["nearest"]["secret"] is False


def test_visit_streak_consecutive_idempotent_and_reset(participant):
    """Série de connexions : +1 si hier, idempotent si déjà aujourd'hui, reset sinon."""
    pid = participant["id"]
    today = local_today().strftime("%Y-%m-%d")
    yest = local_today(-1).strftime("%Y-%m-%d")
    older = local_today(-3).strftime("%Y-%m-%d")

    def state():
        async def _c():
            async with get_db() as db:
                r = await (await db.execute(
                    "SELECT last_visit_date, visit_streak, best_visit_streak FROM participants WHERE id=?",
                    (pid,))).fetchone()
                return dict(r)
        return run(_c())

    def visit(last, cur, best):
        async def _c():
            async with get_db() as db:
                await db.execute(
                    "UPDATE participants SET last_visit_date=?, visit_streak=?, best_visit_streak=? WHERE id=?",
                    (last, cur, best, pid))
                await db.commit()
                p = dict(await (await db.execute("SELECT * FROM participants WHERE id=?", (pid,))).fetchone())
                await _record_daily_visit(db, p)
        run(_c())

    visit(yest, 2, 2)              # dernière visite = hier, série 2
    assert state()["visit_streak"] == 3 and state()["best_visit_streak"] == 3

    visit(today, 3, 3)             # déjà venu aujourd'hui → inchangé
    assert state()["visit_streak"] == 3

    visit(older, 9, 9)             # trou de plusieurs jours → reset à 1, best conservé
    s = state()
    assert s["visit_streak"] == 1 and s["best_visit_streak"] == 9


# --- Tests refresh_trophy_awards ---

def _make_match_and_predict(pid, number, result="team1", score1=2, score2=1,
                            pred="team1", ps1=2, ps2=1, match_date="2000-01-01"):
    async def _create():
        async with get_db() as db:
            cur = await db.execute(
                """INSERT INTO matches (match_number, phase, match_date, kickoff_time,
                   team1_name, team2_name, weight, score_team1, score_team2, result)
                   VALUES (?, 'group', ?, '12:00', 'A', 'B', 1, ?, ?, ?)""",
                (number, match_date, score1, score2, result),
            )
            mid = cur.lastrowid
            await db.execute(
                """INSERT INTO predictions (participant_id, match_id, prediction,
                   exact_score_team1, exact_score_team2)
                   VALUES (?,?,?,?,?)""",
                (pid, mid, pred, ps1, ps2),
            )
            await db.commit()
            return mid
    return run(_create())


def _get_awards(pid):
    async def _q():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT trophy_key, tier FROM trophy_awards WHERE participant_id=? ORDER BY trophy_key, tier",
                (pid,),
            )
            return [(r["trophy_key"], r["tier"]) for r in await rows.fetchall()]
    return run(_q())


def _get_award_timestamps(pid):
    async def _q():
        async with get_db() as db:
            rows = await db.execute(
                "SELECT trophy_key, tier, awarded_at FROM trophy_awards WHERE participant_id=?",
                (pid,),
            )
            return {(r["trophy_key"], r["tier"]): r["awarded_at"] for r in await rows.fetchall()}
    return run(_q())


def test_refresh_trophy_awards_creates_first_step(participant):
    pid = participant["id"]
    _make_match_and_predict(pid, 800001)

    async def _refresh():
        from app.trophies import refresh_trophy_awards
        async with get_db() as db:
            await refresh_trophy_awards(db)
            await db.commit()
    run(_refresh())

    awards = _get_awards(pid)
    assert ("first_step", "_") in awards


def test_refresh_trophy_awards_tiered_stores_all_tiers(participant):
    pid = participant["id"]
    for i in range(5):
        _make_match_and_predict(pid, 810000 + pid * 100 + i,
                                pred="team1", ps1=2, ps2=1,
                                result="team1", score1=2, score2=1)

    async def _refresh():
        from app.trophies import refresh_trophy_awards
        async with get_db() as db:
            await refresh_trophy_awards(db)
            await db.commit()
    run(_refresh())

    awards = _get_awards(pid)
    assert ("sniper", "chocolat") in awards
    assert ("sniper", "bronze") in awards


def test_refresh_trophy_awards_idempotent(participant):
    pid = participant["id"]
    _make_match_and_predict(pid, 820000 + pid)

    async def _refresh():
        from app.trophies import refresh_trophy_awards
        async with get_db() as db:
            await refresh_trophy_awards(db)
            await db.commit()
    run(_refresh())

    ts_before = _get_award_timestamps(pid)

    async def _refresh2():
        from app.trophies import refresh_trophy_awards
        async with get_db() as db:
            await refresh_trophy_awards(db)
            await db.commit()
    run(_refresh2())

    ts_after = _get_award_timestamps(pid)
    for key, ts in ts_before.items():
        assert ts_after[key] == ts


def test_trophy_count_distinct_keys(participant):
    pid = participant["id"]
    for i in range(3):
        _make_match_and_predict(pid, 830000 + pid * 100 + i,
                                pred="team1", ps1=2, ps2=1,
                                result="team1", score1=2, score2=1)

    async def _refresh_and_count():
        from app.trophies import refresh_trophy_awards
        async with get_db() as db:
            await refresh_trophy_awards(db)
            await db.commit()
            row = await db.execute(
                "SELECT COUNT(DISTINCT trophy_key) AS cnt FROM trophy_awards WHERE participant_id=?",
                (pid,),
            )
            return (await row.fetchone())["cnt"]
    count = run(_refresh_and_count())
    awards = _get_awards(pid)
    distinct_keys = len({k for k, _ in awards})
    assert count == distinct_keys
