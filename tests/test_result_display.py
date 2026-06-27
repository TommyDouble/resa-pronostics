from app.result_display import result_score_label, result_score_detail


def _ko(score1, score2, final1=None, final2=None, qualifier=None, result="draw"):
    return {
        "phase": "round_of_16",
        "score_team1": score1,
        "score_team2": score2,
        "final_score_team1": final1 if final1 is not None else score1,
        "final_score_team2": final2 if final2 is not None else score2,
        "qualifier_winner": qualifier,
        "result": result,
        "team1_name": "Espagne",
        "team2_name": "Allemagne",
    }


def test_label_decisive_90_plain():
    match = _ko(2, 1, result="team1", qualifier="team1")
    assert result_score_label(match) == "2–1"


def test_label_extra_time_shows_ap():
    match = _ko(1, 1, final1=2, final2=1, qualifier="team1")
    assert result_score_label(match) == "2–1 a.p."


def test_label_penalties_shows_tab():
    # 1-1 à 90', tranché aux tirs au but (score final inchangé).
    match = _ko(1, 1, qualifier="team1")
    assert result_score_label(match) == "1–1 t.a.b."


def test_label_extra_time_then_penalties_shows_tab():
    # Buts en prolongation mais toujours nul à la fin → tirs au but.
    match = _ko(1, 1, final1=2, final2=2, qualifier="team2")
    assert result_score_label(match) == "2–2 t.a.b."


def test_group_label_is_plain_score():
    match = {"phase": "group", "score_team1": 3, "score_team2": 1, "result": "team1"}
    assert result_score_label(match) == "3–1"


def test_detail_extra_time_keeps_90_and_qualifier():
    match = _ko(1, 1, final1=2, final2=1, qualifier="team1")
    detail = result_score_detail(match)
    assert "90' : 1–1" in detail
    assert "Espagne qualifiée" in detail


def test_detail_penalties_shows_only_qualifier():
    match = _ko(1, 1, qualifier="team1")
    # Pas de ligne « 90' » : le score affiché EST déjà le 90'.
    assert result_score_detail(match) == "Espagne qualifiée"
