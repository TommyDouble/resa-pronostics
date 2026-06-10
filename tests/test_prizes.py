"""Tests du calcul dynamique de la cagnotte."""
from app.prizes import ENTRY_FEE_EUR, PRIZES, compute_prizes


def test_split_totals_100_percent():
    assert sum(p["pct"] for p in PRIZES) == 100


def test_reference_case_50_participants():
    info = compute_prizes(50)
    assert info["pot"] == 500
    amounts = [p["amount"] for p in info["prizes"]]
    assert amounts == [150, 90, 60, 50, 50, 50, 50]


def test_total_always_matches_pot_and_rounds_to_tens():
    for count in range(0, 121):
        info = compute_prizes(count)
        assert info["pot"] == count * ENTRY_FEE_EUR
        amounts = [p["amount"] for p in info["prizes"]]
        assert sum(amounts) == info["pot"], f"count={count}"
        assert all(a % 10 == 0 for a in amounts), f"count={count}"


def test_first_prize_is_never_beaten():
    for count in (1, 7, 23, 37, 50, 64, 99):
        amounts = [p["amount"] for p in compute_prizes(count)["prizes"]]
        assert amounts[0] == max(amounts), f"count={count}"


def test_zero_participants():
    info = compute_prizes(0)
    assert info["pot"] == 0
    assert all(p["amount"] == 0 for p in info["prizes"])
