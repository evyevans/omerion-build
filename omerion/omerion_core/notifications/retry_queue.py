"""Discord notification retry queue (Wave 3.3).

The legacy path swallowed Discord webhook failures silently — a 5xx from
Discord or a transient network blip would lose the notification with
nothing but a `log.warning(...)` to mark it. For HITL cards that's a
real production failure: the review row exists in the DB, but the
founder never sees the prompt and the work stalls until someone notices
the dashboard backlog.

This module replaces the silent swallow with a durable retry queue:

  1. The notification site (`notifications/hitl.py` etc.) calls
     `enqueue(payload)` which writes the row to `notification_outbox`.
  2. The cron sweep in `sweeper.py` (Wave 3.2) picks up due rows,
     retries the Discord POST with exponential backoff (1, 3, 9, 27
     minutes), and either marks them delivered or escalates to a
     permanent_failure that pages the operator via Mission Control.
  3. After `max_attempts` the row is parked for operator review — the
     operator can re-send manually, change the webhook URL, or write
     the message off.

Idempotency: `(notification_type, target_id)` is the natural key. The
same review_id only ever produces one HITL alert in the queue, even
if the call site fires twice.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.util.idempotency import generate_key

log = get_logger("omerion.notifications.retry_queue")


def enqueue(
    *,
    notification_type: str,          # "hitl_review" | "run_completion" | "error_alert"
    target_id: str,                   # review_id, run_id, etc.
    webhook_url: str,
    payload: dict[str, Any],
    max_attempts: int = 4,
) -> str | None:
    """Insert a row into notification_outbox. Returns the outbox_id.

    Idempotent on `(notification_type, target_id)` — a second enqueue for
    the same review/run is a no-op via the UNIQUE constraint (migration
    will be added; for now relies on caller dedupe).
    """
    idempotency_key = generate_key(
        scope=f"notify.{notification_type}",
        payload={"target": target_id, "url_hash": webhook_url[-32:]},
        window="none",  # one-shot per target
    )
    row = {
        "notification_type": notification_type,
        "target_id": target_id,
        "webhook_url": webhook_url,
        "payload": payload,
        "status": "pending",
        "attempt_count": 0,
        "max_attempts": max_attempts,
        "idempotency_key": idempotency_key,
        "next_retry_at": _now_iso(),
    }
    try:
        resp = supabase.table("notification_outbox").upsert(
            row, on_conflict="idempotency_key"
        ).execute()
        rows = resp.data or []
        if not rows:
            return None
        log.info(
            "notify_enqueued",
            type=notification_type,
            target=target_id,
            outbox_id=rows[0].get("outbox_id"),
        )
        return rows[0].get("outbox_id")
    except Exception as exc:  # noqa: BLE001
        # The table may not yet exist on a partially-migrated env. Fall
        # back to direct delivery (loud log) so we never lose the
        # notification entirely.
        log.warning("notify_enqueue_failed_fallback_direct", error=str(exc))
        try:
            import httpx

            with httpx.Client(timeout=5) as c:
                c.post(webhook_url, json=payload)
            return None
        except Exception as inner_exc:  # noqa: BLE001
            log.error("notify_fallback_direct_also_failed", error=str(inner_exc))
            return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["enqueue"]
