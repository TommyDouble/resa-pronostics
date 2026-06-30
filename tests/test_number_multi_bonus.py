import json

from app.scoring import (
    format_number_multi,
    number_multi_bonus_points,
    parse_number_multi,
)


def _answers(mapping):
    """mapping: {pid: (count, [teams])} -> bonus_answers-like rows."""
    return [
        {"participant_id": pid, "answer": json.dumps({"count": c, "teams": t}, ensure_ascii=False)}
        for pid, (c, t) in mapping.items()
    ]


# total = 4 (Maroc + 3 in-race teams qualified)
CORRECT = json.dumps({"count": 4, "teams": ["Sénégal", "Égypte", "Côte d'Ivoire"]}, ensure_ascii=False)


def test_perfect_count_and_teams():
    a = _answers({1: (4, ["Sénégal", "Égypte", "Côte d'Ivoire"])})
    assert number_multi_bonus_points(3, CORRECT, a) == {1: 6}  # 3 + 3


def test_count_right_one_wrong_team():
    # 2 good, 1 wrong → part1 3 + max(2-1,0)=1 → 4
    a = _answers({1: (4, ["Sénégal", "Égypte", "Ghana"])})
    assert number_multi_bonus_points(3, CORRECT, a) == {1: 4}


def test_count_wrong_teams_right():
    # count wrong → part1 0 ; 3 good → +3
    a = _answers({1: (5, ["Sénégal", "Égypte", "Côte d'Ivoire"])})
    assert number_multi_bonus_points(3, CORRECT, a) == {1: 3}


def test_team_part_floored_at_zero():
    # count wrong, all 3 selections wrong → 0 + max(0-3,0)=0
    a = _answers({1: (7, ["Ghana", "Algérie", "RD Congo"])})
    assert number_multi_bonus_points(3, CORRECT, a) == {1: 0}


def test_count_only_no_teams():
    # total = 1 (only Maroc), no teams to pick, count exact → 3
    correct = json.dumps({"count": 1, "teams": []}, ensure_ascii=False)
    a = _answers({1: (1, [])})
    assert number_multi_bonus_points(3, correct, a) == {1: 3}


def test_parse_and_format():
    parsed = parse_number_multi(CORRECT)
    assert parsed["count"] == 4
    assert parsed["teams"] == {"Sénégal", "Égypte", "Côte d'Ivoire"}
    assert parse_number_multi(None) == {"count": None, "teams": set()}
    out = format_number_multi(CORRECT)
    assert "4 au total" in out and "Sénégal" in out
