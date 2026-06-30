import json

from app.scoring import (
    format_team_list,
    multi_select_bonus_points,
    parse_team_set,
)


def _answers(mapping):
    """mapping: {participant_id: [teams]} -> rows like bonus_answers."""
    return [
        {"participant_id": pid, "answer": json.dumps(teams, ensure_ascii=False)}
        for pid, teams in mapping.items()
    ]


CORRECT = json.dumps(["Sénégal", "Égypte", "Côte d'Ivoire"], ensure_ascii=False)


def test_exact_list_scores_full():
    answers = _answers({1: ["Sénégal", "Égypte", "Côte d'Ivoire"]})
    assert multi_select_bonus_points(6, CORRECT, answers) == {1: 6}


def test_one_error_wrong_team():
    # 3 correct teams but one wrong added → 1 error (extra) → 6 - 2 = 4
    answers = _answers({1: ["Sénégal", "Égypte", "Côte d'Ivoire", "Ghana"]})
    assert multi_select_bonus_points(6, CORRECT, answers) == {1: 4}


def test_one_error_forgotten_team():
    # forgot Côte d'Ivoire → 1 error (missing) → 4
    answers = _answers({1: ["Sénégal", "Égypte"]})
    assert multi_select_bonus_points(6, CORRECT, answers) == {1: 4}


def test_two_errors():
    # one wrong added (Ghana) + one forgotten (Côte d'Ivoire) → 2 errors → 6 - 4 = 2
    answers = _answers({1: ["Sénégal", "Égypte", "Ghana"]})
    assert multi_select_bonus_points(6, CORRECT, answers) == {1: 2}


def test_three_errors_floors_at_zero():
    answers = _answers({1: ["Ghana", "Algérie", "RD Congo"]})
    assert multi_select_bonus_points(6, CORRECT, answers) == {1: 0}


def test_custom_error_step():
    answers = _answers({1: ["Sénégal", "Égypte"]})  # 1 error
    cfg = json.dumps({"error_step": 3})
    assert multi_select_bonus_points(6, CORRECT, answers, cfg) == {1: 3}


def test_parse_and_format_helpers():
    assert parse_team_set(CORRECT) == {"Sénégal", "Égypte", "Côte d'Ivoire"}
    assert parse_team_set("Ghana") == {"Ghana"}
    assert parse_team_set(None) == set()
    assert format_team_list(json.dumps(["B", "A"])) == "A, B"
