"""Helpers for participant name casing."""
import re


_SEP_RE = re.compile(r"([-'\u2019])")


def _capitalize_segment(segment: str) -> str:
    lowered = segment.lower()
    return lowered[:1].upper() + lowered[1:] if lowered else ""


def _normalize_word(word: str) -> str:
    parts = _SEP_RE.split(word)
    return "".join(part if _SEP_RE.fullmatch(part) else _capitalize_segment(part) for part in parts)


def normalize_name_part(value: str | None) -> str:
    """Normalize first/last names: spaces, hyphens and apostrophes keep title casing."""
    cleaned = " ".join((value or "").strip().split())
    return " ".join(_normalize_word(word) for word in cleaned.split(" ")) if cleaned else ""


def split_full_name(value: str | None) -> tuple[str, str]:
    normalized = normalize_name_part(value)
    parts = normalized.split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def build_full_name(first_name: str | None, last_name: str | None, fallback: str = "") -> tuple[str, str, str]:
    first = normalize_name_part(first_name)
    last = normalize_name_part(last_name)
    full = f"{first} {last}".strip()
    if not full and fallback:
        first, last = split_full_name(fallback)
        full = f"{first} {last}".strip()
    return first, last, full
