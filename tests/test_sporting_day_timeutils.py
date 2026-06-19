"""Frontières de la journée sportive locale 9 h–8 h 59."""
from datetime import datetime, timezone

import app.timeutils as timeutils


def _utc_from_local(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timeutils.DISPLAY_TZ).astimezone(timezone.utc)


def test_current_sporting_day_switches_at_9am(monkeypatch):
    monkeypatch.setattr(
        timeutils, "now_utc", lambda: _utc_from_local(2026, 6, 19, 8, 59)
    )
    assert timeutils.current_sporting_day() == "2026-06-18"

    monkeypatch.setattr(
        timeutils, "now_utc", lambda: _utc_from_local(2026, 6, 19, 9, 0)
    )
    assert timeutils.current_sporting_day() == "2026-06-19"


def test_sporting_day_bounds_are_local_9am_even_across_dst():
    start, end = timeutils.sporting_day_bounds("2026-10-24")
    start_local = timeutils.parse_utc_iso(start).astimezone(timeutils.DISPLAY_TZ)
    end_local = timeutils.parse_utc_iso(end).astimezone(timeutils.DISPLAY_TZ)
    assert (start_local.hour, start_local.date().isoformat()) == (9, "2026-10-24")
    assert (end_local.hour, end_local.date().isoformat()) == (9, "2026-10-25")
