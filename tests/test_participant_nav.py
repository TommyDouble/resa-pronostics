"""Contrat de rendu de la navigation principale des participants."""

import re
from pathlib import Path

from app.templating import create_templates


def _render_mobile_nav(active_nav: str, pending_bonus: int = 0) -> str:
    template = create_templates().env.from_string(
        """{% from "_macros.html" import participant_nav %}
        {{ participant_nav("test-token", active_nav, pending_bonus, "mobile") }}"""
    )
    return template.render(active_nav=active_nav, pending_bonus=pending_bonus)


def test_mobile_nav_renders_five_destinations_and_one_active_pill():
    html = _render_mobile_nav("rank")

    assert html.count("<a ") == 5
    assert len(re.findall(r"<a [^>]*aria-label=", html)) == 5
    assert html.count('aria-current="page"') == 1
    assert html.count('class="bnav-active-bg"') == 1
    assert 'class="bnav-active-bg" aria-hidden="true"' in html
    assert 'aria-label="Accueil"' in html
    assert 'aria-label="Pronos"' in html
    assert 'aria-label="Classement"' in html
    assert 'aria-label="Profil"' in html
    assert 'aria-label="Bonus"' in html
    assert "bnav-label" not in html


def test_mobile_nav_has_no_active_pill_on_contextual_page():
    html = _render_mobile_nav("rules")

    assert 'aria-current="page"' not in html
    assert 'class="bnav-active-bg"' not in html


def test_mobile_nav_keeps_bonus_badge_with_active_pill():
    html = _render_mobile_nav("bonus", pending_bonus=2)

    assert html.count('class="bnav-active-bg"') == 1
    assert html.count("nicon-wrap bdg") == 1
    assert 'class="nicon ic-bonus"' in html


def test_mobile_nav_view_transition_stays_above_match_cards():
    css = Path("app/static/css/resa.css").read_text()

    assert re.search(
        r"::view-transition-group\(bottom-nav\)\s*\{[^}]*z-index:\s*100",
        css,
        re.DOTALL,
    )
    assert re.search(
        r"::view-transition-group\(bottom-nav-active-pill\)\s*\{[^}]*z-index:\s*101",
        css,
        re.DOTALL,
    )
