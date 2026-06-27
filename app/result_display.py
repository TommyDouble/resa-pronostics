"""Shared formatting helpers for official match results."""


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _score_pair(score1, score2, sep: str = "–") -> str:
    if score1 is None or score2 is None:
        return ""
    return f"{score1}{sep}{score2}"


def _score90(match) -> tuple[int | None, int | None]:
    return _row_get(match, "score_team1"), _row_get(match, "score_team2")


def _final_score(match) -> tuple[int | None, int | None]:
    score1, score2 = _score90(match)
    final1 = _row_get(match, "final_score_team1", score1)
    final2 = _row_get(match, "final_score_team2", score2)
    return final1, final2


def qualified_team_name(match) -> str:
    qualifier = _row_get(match, "qualifier_winner")
    if qualifier == "team1":
        return _row_get(match, "team1_name", "") or ""
    if qualifier == "team2":
        return _row_get(match, "team2_name", "") or ""
    return ""


def result_score_label(match) -> str:
    """Primary official score label.

    `score_team1/score_team2` remain the 90-minute score used for pronostics.
    In knockout matches with goals after 90 minutes, the primary public label is
    the final score with an "a.p." suffix.
    """
    score1, score2 = _score90(match)
    if score1 is None or score2 is None:
        return ""
    final1, final2 = _final_score(match)
    if (
        _row_get(match, "phase") != "group"
        and final1 is not None
        and final2 is not None
        and (final1, final2) != (score1, score2)
    ):
        return f"{_score_pair(final1, final2)} a.p."
    return _score_pair(score1, score2)


def result_score_detail(match) -> str:
    """Secondary result detail for cards and small summaries."""
    score1, score2 = _score90(match)
    if score1 is None or score2 is None:
        return ""
    if _row_get(match, "phase") == "group":
        return "Score final"

    final1, final2 = _final_score(match)
    parts: list[str] = []
    if final1 is not None and final2 is not None and (final1, final2) != (score1, score2):
        parts.append(f"90' : {_score_pair(score1, score2)}")
    qualifier = qualified_team_name(match)
    if qualifier:
        parts.append(f"{qualifier} qualifiée")
    return " · ".join(parts) if parts else "Score final"


def result_full_label(match) -> str:
    """Compact single-line result for admin tables and textual details."""
    score = result_score_label(match)
    if not score:
        return ""
    detail = result_score_detail(match)
    if detail and detail != "Score final":
        return f"{score} ({detail})"
    return score
