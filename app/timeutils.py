"""UTC storage and Europe/Brussels display helpers."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import settings

DISPLAY_TZ = ZoneInfo(settings.TZ_DISPLAY)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_utc_iso() -> str:
    return now_utc().replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def parse_utc_iso(value: str) -> datetime:
    cleaned = (value or "").strip().replace(" ", "T")
    parsed = datetime.fromisoformat(cleaned)
    return _as_utc(parsed)


def utc_iso(value: datetime) -> str:
    return _as_utc(value).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def match_kickoff_utc(match: dict) -> datetime:
    return parse_utc_iso(f"{match['match_date']}T{match['kickoff_time']}")


def is_match_locked(match: dict) -> bool:
    try:
        return now_utc() >= match_kickoff_utc(match)
    except Exception:
        return False


def minutes_until_match(match: dict) -> int:
    try:
        return int((match_kickoff_utc(match) - now_utc()).total_seconds() / 60)
    except Exception:
        return 99999


# Durée max estimée d'un match (90' + arrêts + prolongations + TAB).
LIVE_WINDOW_MINUTES = 150


def match_live_state(match: dict) -> str:
    """'' (à venir) | 'live' | 'awaiting' (joué, résultat pas encodé) | 'done'."""
    if not is_match_locked(match):
        return ""
    if match.get("result") is not None:
        return "done"
    return "live" if minutes_until_match(match) >= -LIVE_WINDOW_MINUTES else "awaiting"


def local_input_to_utc_iso(value: str, timezone_name: str | None = None) -> str:
    local = datetime.fromisoformat(value.strip())
    if local.tzinfo is None:
        try:
            input_tz = ZoneInfo(timezone_name) if timezone_name else DISPLAY_TZ
        except Exception:
            input_tz = DISPLAY_TZ
        local = local.replace(tzinfo=input_tz)
    return utc_iso(local)


def local_today(offset_days: int = 0) -> date:
    """Date locale (Brussels) avec décalage en jours (−1 = hier)."""
    return (now_utc().astimezone(DISPLAY_TZ) + timedelta(days=offset_days)).date()


def utc_day_bounds_for_local_date(day: date | None = None) -> tuple[str, str]:
    local_day = day or now_utc().astimezone(DISPLAY_TZ).date()
    local_start = datetime.combine(local_day, time.min, tzinfo=DISPLAY_TZ)
    local_end = datetime.combine(local_day, time.max, tzinfo=DISPLAY_TZ)
    return utc_iso(local_start), utc_iso(local_end)


def format_local_datetime(value: str, fmt: str = "%d/%m/%Y %H:%M") -> str:
    try:
        return parse_utc_iso(value).astimezone(DISPLAY_TZ).strftime(fmt)
    except Exception:
        return value or ""


def format_local_input(value: str) -> str:
    return format_local_datetime(value, "%Y-%m-%dT%H:%M")


def format_utc_iso_z(value: str) -> str:
    try:
        return parse_utc_iso(value).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return value or ""


def format_match_utc_iso_z(match: dict) -> str:
    try:
        return match_kickoff_utc(match).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def format_match_local_time(match: dict) -> str:
    try:
        return match_kickoff_utc(match).astimezone(DISPLAY_TZ).strftime("%H:%M")
    except Exception:
        return (match.get("kickoff_time") or "")[:5]


def format_match_local_date(match: dict) -> str:
    try:
        return match_kickoff_utc(match).astimezone(DISPLAY_TZ).strftime("%Y-%m-%d")
    except Exception:
        return match.get("match_date") or ""


def format_match_local_datetime(match: dict) -> str:
    try:
        return match_kickoff_utc(match).astimezone(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return f"{match.get('match_date', '')} {(match.get('kickoff_time') or '')[:5]}".strip()
