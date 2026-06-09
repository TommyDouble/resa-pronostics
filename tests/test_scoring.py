from app.scoring import (
    answers_match,
    calculate_match_score,
    calculate_pre_tournament_points,
)


def make_match(**overrides):
    match = {"result": "team1", "score_team1": 2, "score_team2": 1, "weight": 1}
    match.update(overrides)
    return match


def make_prediction(**overrides):
    pred = {"prediction": "team1", "exact_score_team1": 2, "exact_score_team2": 1}
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

    def test_no_result(self):
        assert calculate_match_score(make_prediction(), make_match(result=None)) == 0


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
