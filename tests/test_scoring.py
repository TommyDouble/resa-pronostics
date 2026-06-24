import json

from app.scoring import (
    answers_match,
    calculate_finalists_points,
    calculate_match_score,
    calculate_pre_tournament_points,
    closest_podium_bonus_points,
    parse_revelation_winners,
)


def make_match(**overrides):
    match = {
        "phase": "group",
        "result": "team1",
        "score_team1": 2,
        "score_team2": 1,
        "weight": 1,
        "qualifier_winner": None,
    }
    match.update(overrides)
    return match


def make_prediction(**overrides):
    pred = {
        "prediction": "team1",
        "exact_score_team1": 2,
        "exact_score_team2": 1,
        "qualifier_prediction": None,
    }
    pred.update(overrides)
    return pred


class TestMatchScore:
    def test_exact_score(self):
        assert calculate_match_score(make_prediction(), make_match()) == 4

    def test_correct_outcome_wrong_score(self):
        pred = make_prediction(exact_score_team1=3, exact_score_team2=0)
        assert calculate_match_score(pred, make_match()) == 2

    def test_wrong_outcome(self):
        pred = make_prediction(prediction="team2")
        assert calculate_match_score(pred, make_match()) == 0

    def test_weighted_match(self):
        assert calculate_match_score(make_prediction(), make_match(weight=2)) == 6

    def test_weighted_correct_outcome_wrong_score(self):
        pred = make_prediction(exact_score_team1=1, exact_score_team2=0)
        assert calculate_match_score(pred, make_match(weight=2)) == 4

    def test_no_result(self):
        assert calculate_match_score(make_prediction(), make_match(result=None)) == 0

    def test_knockout_draw_prediction_gets_winner_points_if_actual_winner_matches(self):
        pred = make_prediction(
            prediction="draw",
            exact_score_team1=2,
            exact_score_team2=2,
            qualifier_prediction="team1",
        )
        match = make_match(
            phase="round_of_16",
            result="team1",
            score_team1=3,
            score_team2=2,
            weight=2,
            qualifier_winner="team1",
        )
        assert calculate_match_score(pred, match) == 4

    def test_knockout_draw_prediction_scores_zero_if_actual_winner_differs(self):
        pred = make_prediction(
            prediction="draw",
            exact_score_team1=2,
            exact_score_team2=2,
            qualifier_prediction="team1",
        )
        match = make_match(
            phase="round_of_16",
            result="team2",
            score_team1=2,
            score_team2=3,
            weight=2,
            qualifier_winner="team2",
        )
        assert calculate_match_score(pred, match) == 0

    def test_knockout_exact_draw_scores_only_with_correct_qualifier(self):
        pred = make_prediction(
            prediction="draw",
            exact_score_team1=2,
            exact_score_team2=2,
            qualifier_prediction="team1",
        )
        match = make_match(
            phase="round_of_16",
            result="draw",
            score_team1=2,
            score_team2=2,
            weight=2,
            qualifier_winner="team1",
        )
        assert calculate_match_score(pred, match) == 6

    def test_knockout_exact_draw_with_wrong_qualifier_scores_zero(self):
        pred = make_prediction(
            prediction="draw",
            exact_score_team1=2,
            exact_score_team2=2,
            qualifier_prediction="team1",
        )
        match = make_match(
            phase="round_of_16",
            result="draw",
            score_team1=2,
            score_team2=2,
            weight=2,
            qualifier_winner="team2",
        )
        assert calculate_match_score(pred, match) == 0


class TestPreTournamentPoints:
    def question(self, key, points=5, answer="France"):
        return {"key": key, "points_value": points, "correct_answer": answer}

    def test_winner_correct(self):
        assert calculate_pre_tournament_points(self.question("winner", 8), "France") == 8

    def test_winner_wrong(self):
        assert calculate_pre_tournament_points(self.question("winner", 8), "Brésil") == 0

    def test_no_answer_set(self):
        question = self.question("winner", 8, answer=None)
        assert calculate_pre_tournament_points(question, "France") == 0

    def test_empty_prediction(self):
        assert calculate_pre_tournament_points(self.question("winner", 8), "") == 0

    def test_total_goals_exact(self):
        question = self.question("total_goals", 8, answer="140")
        assert calculate_pre_tournament_points(question, 140) == 8

    def test_total_goals_near(self):
        question = self.question("total_goals", 8, answer="140")
        assert calculate_pre_tournament_points(question, 143) == 4
        assert calculate_pre_tournament_points(question, 137) == 4

    def test_total_goals_off(self):
        question = self.question("total_goals", 8, answer="140")
        assert calculate_pre_tournament_points(question, 144) == 0
        assert calculate_pre_tournament_points(question, 100) == 0

    def test_two_finalists_score_even_when_ordered_normally(self):
        prediction = {"winner": "Argentine", "finalist": "France"}
        correct = {"winner": "Argentine", "finalist": "France"}

        assert calculate_finalists_points(prediction, correct) == 14

    def test_two_finalists_score_even_when_champion_is_inverted(self):
        prediction = {"winner": "France", "finalist": "Argentine"}
        correct = {"winner": "Argentine", "finalist": "France"}

        assert calculate_finalists_points(prediction, correct) == 14

    def test_one_finalist_scores_seven_points(self):
        prediction = {"winner": "Argentine", "finalist": "Brésil"}
        correct = {"winner": "Argentine", "finalist": "France"}

        assert calculate_finalists_points(prediction, correct) == 7

    def test_finalist_question_uses_both_finalist_picks(self):
        question = self.question("finalist", 7, answer="France")
        prediction = {"winner": "France", "finalist": "Argentine"}
        correct = {"winner": "Argentine", "finalist": "France"}

        assert (
            calculate_pre_tournament_points(
                question,
                "Argentine",
                prediction=prediction,
                correct_answers=correct,
            )
            == 14
        )

    def test_revelation_pick_in_winning_set_scores(self):
        question = self.question("revelation", 5, answer=json.dumps(["Maroc", "Japon"]))
        assert calculate_pre_tournament_points(question, "Maroc") == 5
        assert calculate_pre_tournament_points(question, "Japon") == 5

    def test_revelation_pick_outside_winning_set_scores_zero(self):
        question = self.question("revelation", 5, answer=json.dumps(["Maroc", "Japon"]))
        assert calculate_pre_tournament_points(question, "Sénégal") == 0

    def test_revelation_legacy_single_answer_still_scores(self):
        question = self.question("revelation", 5, answer="Maroc")
        assert calculate_pre_tournament_points(question, "Maroc") == 5
        assert calculate_pre_tournament_points(question, "Japon") == 0


class TestRevelationWinners:
    def test_parse_json_list(self):
        assert parse_revelation_winners(json.dumps(["Maroc", "Japon"])) == {"Maroc", "Japon"}

    def test_parse_legacy_single(self):
        assert parse_revelation_winners("Maroc") == {"Maroc"}

    def test_parse_empty(self):
        assert parse_revelation_winners("") == set()
        assert parse_revelation_winners(None) == set()
        assert parse_revelation_winners("[]") == set()


class TestAnswersMatch:
    def test_number_formats(self):
        assert answers_match("number", "10", "10")
        assert answers_match("number", "10.0", "10")
        assert answers_match("number", "10,5", "10.5")
        assert not answers_match("number", "11", "10")

    def test_text_case_insensitive(self):
        assert answers_match("text", "  mbappé ", "Mbappé")
        assert not answers_match("text", "Kane", "Mbappé")

    def test_choice_exact(self):
        assert answers_match("choice", "Oui", "Oui")
        assert not answers_match("choice", "oui", "Oui")

    def test_empty(self):
        assert not answers_match("text", "", "x")
        assert not answers_match("text", "x", "")


class TestClosestPodiumBonusPoints:
    def answer(self, participant_id, answer):
        return {"participant_id": participant_id, "answer": answer}

    def test_unique_podium(self):
        scores = closest_podium_bonus_points(
            6,
            "60",
            [
                self.answer(1, "60"),
                self.answer(2, "59"),
                self.answer(3, "62"),
                self.answer(4, "70"),
            ],
        )

        assert scores == {1: 6, 2: 4, 3: 2, 4: 0}

    def test_generous_tie_skips_following_rank(self):
        scores = closest_podium_bonus_points(
            6,
            "60",
            [
                self.answer(1, "59"),
                self.answer(2, "61"),
                self.answer(3, "62"),
                self.answer(4, "63"),
            ],
        )

        assert scores == {1: 6, 2: 6, 3: 2, 4: 0}

    def test_invalid_numeric_answers_score_zero(self):
        scores = closest_podium_bonus_points(
            6,
            "60",
            [self.answer(1, "beaucoup"), self.answer(2, "60")],
        )

        assert scores == {1: 0, 2: 6}
