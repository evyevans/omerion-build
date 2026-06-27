"""ISO datetime parsing helpers shared across agents.

Why this lives in core: prior to extraction, four agents each carried their own
copy of the `datetime.fromisoformat(s.replace("Z", "+00:00"))` dance, with
inconsistent error handling (some returned None, some defaulted silently to
30 or 60 days). Centralizing keeps the parse rule and the failure mode in one
place so a fix to either propagates everywhere.
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_iso_utc(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into a tz-aware UTC datetime, or return None.

    Accepts the trailing-Z form Postgres/Supabase emit ("2026-05-02T12:34:56Z")
    by mapping it to "+00:00" before handing to fromisoformat. Returns None on
    empty/invalid input so callers can decide whether to default, log, or raise.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def days_since(value: str | None, *, default: float = 999.0) -> float:
    """Days between now (UTC) and a parsed timestamp; default when input is bad.

    Use when the caller wants a numeric bucket and a missing/malformed value
    should bias the contact toward the cold/old end of the range.
    """
    dt = parse_iso_utc(value)
    if dt is None:
        return default
    return float((datetime.now(timezone.utc) - dt).days)
