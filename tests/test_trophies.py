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
        "exact": 0, "bonus_king": False, "near_miss": 0, "longest_streak": 0,
        "draw_correct": 0, "perfect_day": False,
    }


def test_evaluate_shape_and_categories():
    trophies = evaluate(_empty_metrics())
    assert len(trophies) == 10
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


def test_secret_perfect_day_hidden_until_unlocked():
    m = _empty_metrics()
    pd = _by_key(evaluate(m))["perfect_day"]
    assert pd["secret"] is True and pd["unlocked"] is False
    m["perfect_day"] = True
    assert _by_key(evaluate(m))["perfect_day"]["unlocked"] is True


def test_summarize_nearest_is_highest_progress_locked():
    m = _empty_metrics()
    m["exact"] = 4         # sniper 4/5 = 0.8 (le plus proche)
    m["draw_correct"] = 1  # roi du nul 1/3
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
