"""W7 — Cabinet à trophées : logique pure app.trophies + rendu du cabinet."""
from app.trophies import evaluate, summarize, CATEGORIES


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
