"""UTC storage and Europe/Brussels display helpers."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings

DISPLAY_TZ = ZoneInfo(settings.TZ_DISPLAY)
UTC_FORMAT = "%Y-%m-%dT%H:%M:%S"


def now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime(UTC_FORMAT)


def parse_utc(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Missing datetime")
    raw = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_input_to_utc(value: str) -> str:
    dt = datetime.fromisoformat(value.strip())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DISPLAY_TZ)
    return dt.astimezone(timezone.utc).strftime(UTC_FORMAT)


def utc_to_local_input(value: str) -> str:
    return parse_utc(value).astimezone(DISPLAY_TZ).strftime("%Y-%m-%dT%H:%M")


def utc_to_local_label(value: str) -> str:
    return parse_utc(value).astimezone(DISPLAY_TZ).strftime("%d/%m/%Y %H:%M")


def utc_to_local_short(value: str) -> str:
    return parse_utc(value).astimezone(DISPLAY_TZ).strftime("%d/%m %H:%M")


def match_to_local_label(match: dict) -> str:
    return utc_to_local_short(f"{match['match_date']}T{match['kickoff_time']}")
