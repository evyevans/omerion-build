"""Deterministic idempotency-key generation.

The core invariant of Wave 1: **every external write and every event
emission carries an `idempotency_key` that is reproducible from the
business identity of the action.** Two attempts to perform the same
logical operation produce the same key, so a Postgres UNIQUE constraint
(migration 0040) makes the second attempt a no-op.

Design:

  * Keys are SHA-256 of a canonical JSON serialization. Canonical means:
    sorted keys, no whitespace, UTF-8. Two equivalent dicts produce the
    same hash regardless of insertion order.
  * Time windows let the caller bucket repeated actions. For example,
    "score contact X" is idempotent *per day* — same key today, different
    key tomorrow. Windows are 'day', 'hour', or 'minute' (rounded down).
  * A `scope` prefix prevents cross-domain key collisions. E.g. an
    outbound message and a contact score with otherwise-identical payloads
    do not collide because their scopes differ.

Keys are 64 hex chars (full SHA-256). Postgres UNIQUE handles them fine.
Callers that need a shorter form for human-readable identifiers can
truncate to first N hex chars — the wrapper does NOT truncate when
writing to DB.

Schema integration:
  Every `EventEnvelope` subclass exposes `natural_key` — the wrapper
  passes that string as the `payload` arg to `generate_key()` for
  consistent event-level idempotency.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

Window = Literal["day", "hour", "minute", "none"]


def _time_bucket(window: Window, at: datetime | None = None) -> str:
    """Return a stable string for the current time bucket.

    Examples:
      window='day'    → '2026-05-23'
      window='hour'   → '2026-05-23T17'
      window='minute' → '2026-05-23T17:42'
      window='none'   → '' (no time component — single-shot keys)
    """
    if window == "none":
        return ""
    now = at or datetime.now(timezone.utc)
    if window == "day":
        return now.date().isoformat()
    if window == "hour":
        return now.strftime("%Y-%m-%dT%H")
    if window == "minute":
        return now.strftime("%Y-%m-%dT%H:%M")
    raise ValueError(f"unknown idempotency window: {window!r}")


def _canonical(payload: Any) -> str:
    """Stable serialization. Dicts/lists are recursed; strings pass through."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (int, float, bool)) or payload is None:
        return json.dumps(payload)
    # Pydantic models can be passed; .model_dump() if present, else dict().
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def generate_key(
    scope: str,
    payload: Any,
    window: Window = "none",
    *,
    at: datetime | None = None,
) -> str:
    """Return a deterministic SHA-256 hex key.

    Args:
      scope: Domain prefix, e.g. 'outbound.linkedin', 'contact.scored',
             'business_outcome'. Prevents cross-domain collisions.
      payload: The business identity. Can be a string (e.g. a UUID), a dict,
               or a Pydantic model. For Pydantic event models, pass
               `event.natural_key` (already a string).
      window: Time bucket. Choose based on the *re-processing* semantics:
              - 'none'    → one-shot (e.g. an immutable record_id)
              - 'day'     → re-processable daily (e.g. daily score refresh)
              - 'hour'    → re-processable hourly
              - 'minute'  → near-real-time dedupe (e.g. Discord message dedupe)
      at: Optional anchor time for testing; defaults to now.

    Returns:
      64-char SHA-256 hex digest. Safe to store in a Postgres TEXT or
      BYTEA column with UNIQUE constraint.
    """
    if not scope:
        raise ValueError("scope must be non-empty")
    bucket = _time_bucket(window, at)
    canonical = _canonical(payload)
    seed = f"{scope}|{canonical}|{bucket}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def generate_event_key(event_type: str, natural_key: str, window: Window = "day") -> str:
    """Convenience wrapper for event-level idempotency.

    Uses the event_type as scope and the schema's `natural_key` as payload.
    Defaults to a 'day' window — most events are re-emittable once per day
    (e.g. CONTACT_SCORED for the same contact tomorrow is a legitimate
    re-emission). Override window when the event is truly one-shot
    (e.g. BLUEPRINT_APPROVED → window='none').
    """
    return generate_key(event_type, natural_key, window=window)


def generate_run_key(
    skill: str,
    business_entity_id: str | None,
    *,
    trigger: str,
    window: Window = "minute",
) -> str:
    """Convenience wrapper for run-level idempotency.

    The wrapper uses this in Stage 2 to dedupe rapid-fire trigger storms
    (e.g. a Discord user hammering the same message twice in 5 seconds).
    Default window is 'minute' — same (skill, entity, trigger) within the
    same minute → same key → dedup.

    `business_entity_id` should be a stable identifier of the *subject* of
    the run: contact_id for outreach agents, account_id for scrapers,
    blueprint_id for build_orchestrator, etc. Use `None` for skill-level
    deduplication (e.g. cron-scheduled R&D sweeps).
    """
    payload = {
        "skill": skill,
        "entity": business_entity_id or "",
        "trigger": trigger,
    }
    return generate_key(f"run.{skill}", payload, window=window)


__all__ = [
    "Window",
    "generate_key",
    "generate_event_key",
    "generate_run_key",
]
