import json

from app.database import QUARTER_COMEBACK_QUALIFIERS_CONFIG
from app.scoring import (
    format_number_multi,
    normalize_number_multi_config,
    number_multi_bonus_points,
    parse_number_multi,
)


def _answers(mapping):
    """mapping: {pid: (count, [teams])} -> bonus_answers-like rows."""
    return [
        {"participant_id": pid, "answer": json.dumps({"count": c, "teams": t}, ensure_ascii=False)}
        for pid, (c, t) in mapping.items()
    ]


CONFIG = json.dumps({
    "locked_teams": ["Maroc"],
    "min_count": 1,
    "max_count": 8,
    "part1_points": 3,
    "team_step": 1,
    "max_points": 10,
}, ensure_ascii=False)

ROUND16_CONFIG = json.dumps({
    "locked_teams": [],
    "min_count": 0,
    "max_count": 3,
    "part1_points": 4,
    "team_step": 2,
    "max_points": 10,
}, ensure_ascii=False)
QUARTER_CONFIG = json.dumps(QUARTER_COMEBACK_QUALIFIERS_CONFIG, ensure_ascii=False)

# total = 4 (Maroc + 3 in-race teams qualified)
CORRECT = json.dumps(
    {"count": 4, "teams": ["Maroc", "Sénégal", "Égypte", "Côte d'Ivoire"]},
    ensure_ascii=False,
)


def test_perfect_count_and_teams():
    a = _answers({1: (4, ["Maroc", "Sénégal", "Égypte", "Côte d'Ivoire"])})
    assert number_multi_bonus_points(10, CORRECT, a, CONFIG) == {1: 6}  # 3 + 3


def test_count_right_one_wrong_team():
    # 2 good, 1 wrong → part1 3 + max(2-1,0)=1 → 4
    a = _answers({1: (4, ["Maroc", "Sénégal", "Égypte", "Ghana"])})
    assert number_multi_bonus_points(10, CORRECT, a, CONFIG) == {1: 4}


def test_count_wrong_teams_right():
    # count wrong → part1 0 ; 3 good → +3
    a = _answers({1: (5, ["Maroc", "Sénégal", "Égypte", "Côte d'Ivoire"])})
    assert number_multi_bonus_points(10, CORRECT, a, CONFIG) == {1: 3}


def test_team_part_floored_at_zero():
    # count wrong, all 3 selections wrong → 0 + max(0-3,0)=0
    a = _answers({1: (4, ["Maroc", "Ghana", "Algérie", "RD Congo"])})
    assert number_multi_bonus_points(10, CORRECT, a, CONFIG) == {1: 3}


def test_count_only_no_teams():
    # total = 1 (only Maroc), no teams to pick, count exact → 3
    correct = json.dumps({"count": 1, "teams": ["Maroc"]}, ensure_ascii=False)
    a = _answers({1: (1, ["Maroc"])})
    assert number_multi_bonus_points(10, correct, a, CONFIG) == {1: 3}


def test_locked_team_gives_no_free_team_point():
    correct = json.dumps({"count": 4, "teams": ["Maroc", "Sénégal", "Égypte", "Ghana"]}, ensure_ascii=False)
    a = _answers({1: (2, ["Maroc"])})
    assert number_multi_bonus_points(10, correct, a, CONFIG) == {1: 0}


def test_max_points_config_for_africa():
    teams = ["Maroc", "Côte d'Ivoire", "RD Congo", "Sénégal", "Algérie", "Égypte", "Cap-Vert", "Ghana"]
    cfg = normalize_number_multi_config(
        10,
        CONFIG,
        teams,
    )
    assert cfg["max_points"] == 10
    correct = json.dumps({"count": 8, "teams": teams}, ensure_ascii=False)
    assert number_multi_bonus_points(10, correct, _answers({1: (8, teams)}), CONFIG) == {1: 10}


def test_parse_and_format():
    parsed = parse_number_multi(CORRECT)
    assert parsed["count"] == 4
    assert parsed["teams"] == {"Maroc", "Sénégal", "Égypte", "Côte d'Ivoire"}
    assert parse_number_multi(None) == {"count": None, "teams": set()}
    out = format_number_multi(
        CORRECT,
        ["Maroc", "Côte d'Ivoire", "RD Congo", "Sénégal", "Algérie", "Égypte"],
    )
    assert out == "4 au total — Maroc, Côte d'Ivoire, Sénégal, Égypte"


def test_round16_number_multi_accepts_zero_without_teams():
    correct = json.dumps({"count": 0, "teams": []}, ensure_ascii=False)
    answers = _answers({1: (0, [])})

    assert number_multi_bonus_points(10, correct, answers, ROUND16_CONFIG) == {1: 4}


def test_round16_number_multi_perfect_answers_for_0_to_3_teams():
    scenarios = [
        (0, [], 4),
        (1, ["Canada"], 6),
        (2, ["Canada", "Mexique"], 8),
        (3, ["Canada", "Mexique", "États-Unis"], 10),
    ]
    for count, teams, expected in scenarios:
        correct = json.dumps({"count": count, "teams": teams}, ensure_ascii=False)
        answers = _answers({1: (count, teams)})
        assert number_multi_bonus_points(10, correct, answers, ROUND16_CONFIG) == {1: expected}


def test_round16_number_multi_exact_count_with_wrong_selection():
    correct = json.dumps({"count": 2, "teams": ["Canada", "Mexique"]}, ensure_ascii=False)
    answers = _answers({1: (2, ["Canada", "États-Unis"])})

    assert number_multi_bonus_points(10, correct, answers, ROUND16_CONFIG) == {1: 4}


def test_round16_number_multi_wrong_count_with_good_selection():
    correct = json.dumps({"count": 2, "teams": ["Canada", "Mexique"]}, ensure_ascii=False)
    answers = _answers({1: (3, ["Canada", "Mexique"])})

    assert number_multi_bonus_points(10, correct, answers, ROUND16_CONFIG) == {1: 4}


def test_round16_number_multi_detail_cannot_go_negative():
    correct = json.dumps({"count": 2, "teams": ["Canada", "Mexique"]}, ensure_ascii=False)
    answers = _answers({1: (3, ["États-Unis"])})

    assert number_multi_bonus_points(10, correct, answers, ROUND16_CONFIG) == {1: 0}


def test_quarter_number_multi_uses_two_point_count_and_team_steps():
    correct = json.dumps(
        {"count": 4, "teams": ["France", "Brésil", "Maroc", "Japon"]},
        ensure_ascii=False,
    )

    perfect = _answers({1: (4, ["France", "Brésil", "Maroc", "Japon"])})
    assert number_multi_bonus_points(10, correct, perfect, QUARTER_CONFIG) == {1: 10}

    zero_comeback = json.dumps({"count": 0, "teams": []}, ensure_ascii=False)
    assert number_multi_bonus_points(
        10,
        zero_comeback,
        _answers({1: (0, [])}),
        QUARTER_CONFIG,
    ) == {1: 2}

    wrong_team = _answers({1: (4, ["France", "Brésil", "Maroc", "Espagne"])})
    assert number_multi_bonus_points(10, correct, wrong_team, QUARTER_CONFIG) == {1: 6}

    wrong_count_good_teams = _answers({1: (3, ["France", "Brésil", "Maroc"])})
    assert number_multi_bonus_points(10, correct, wrong_count_good_teams, QUARTER_CONFIG) == {1: 6}

    detail_floored = _answers({1: (2, ["Espagne", "Italie"])})
    assert number_multi_bonus_points(10, correct, detail_floored, QUARTER_CONFIG) == {1: 0}


def test_team_pairs_flag_passes_through_normalize_config():
    assert normalize_number_multi_config(10, QUARTER_COMEBACK_QUALIFIERS_CONFIG, None)["team_pairs"] is True

    without_flag = json.dumps({"part1_points": 2, "team_step": 2}, ensure_ascii=False)
    assert normalize_number_multi_config(10, without_flag, None)["team_pairs"] is False
