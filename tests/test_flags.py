"""Chaque équipe qualifiée doit avoir son drapeau."""
from app.flags import TEAM_FLAGS, team_label
from app.players import TEAMS_48


def test_all_48_teams_have_a_flag():
    missing = [t for t in TEAMS_48 if t not in TEAM_FLAGS]
    assert missing == []


def test_placeholders_stay_unchanged():
    assert team_label("Vainqueur M74") == "Vainqueur M74"
    assert team_label("Belgique") == "🇧🇪 Belgique"
