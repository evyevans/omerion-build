"""Sweeper — the system's loud-failure heartbeat.

Wave 3.2. Three cron jobs registered into the existing APScheduler in
`omerion/main.py`:

  1. **Stuck-run sweep** (every 5 min): finds `agent_runs` rows that
     have outlived their wall-clock timeout (status='running' for >30 min)
     or HITL backlog SLA (status='hitl_waiting' for >48 h). The stuck
     run is marked `failed` with a sweeper-tagged error so the operator
     can distinguish a real exception from a janitor close-out.

  2. **HITL expiration sweep** (every 5 min): finds `founder_review_queue`
     rows past their `expires_at` and marks them `expired`. A Mission
     Control alert fires so the operator knows the request is dropping
     past SLA.

  3. **DLQ retry sweep** (every 10 min): finds `event_dead_letter` rows
     with `status='pending'` and `next_retry_at <= now()`, attempts to
     re-dispatch the original event, and either marks the row
     `delivered` on success or bumps `attempt_count` and schedules the
     next retry with exponential backoff (2, 4, 8, 16, 32 minutes).

Every sweeper job is **idempotent**: running it twice in a row is
identical to running it once. This matters because we run inside
APScheduler in the api process — a Railway restart that happens to be
mid-tick won't double-process rows.

Each job fail-loops: if a single row raises, we log the row id and
continue with the rest. A full sweeper failure would silently let the
backlog grow, which is the opposite of what this module exists for.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.sweeper")


# Configuration. Tune in settings.py later; hardcoded for Wave 3.2.
STUCK_RUNNING_TIMEOUT_MINUTES   = 30   # matches the ThreadPoolExecutor cap
STUCK_HITL_TIMEOUT_HOURS        = 48
DLQ_RETRY_DELAYS_MINUTES        = (2, 4, 8, 16, 32)  # exponential backoff


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────── stuck runs ───────────────────────────


def sweep_stuck_runs() -> dict[str, int]:
    """Find and close out `agent_runs` rows that have outlived their SLA.

    Two cohorts:
      * status='running' AND started_at < now() - 30 min
      * status='hitl_waiting' AND created_at < now() - 48 hr

    Both are transitioned to `failed` with `error = "sweeper:..."` so the
    operator can grep for sweeper-killed runs in the error column.

    Returns counts for Mission Control.
    """
    now = datetime.now(timezone.utc)
    running_cutoff = (now - timedelta(minutes=STUCK_RUNNING_TIMEOUT_MINUTES)).isoformat()
    hitl_cutoff = (now - timedelta(hours=STUCK_HITL_TIMEOUT_HOURS)).isoformat()

    closed_running = 0
    closed_hitl = 0

    try:
        stuck_running = (
            supabase.table("agent_runs")
            .select("run_id,agent_name,started_at,hitl_expires_at")
            .eq("status", "running")
            .lt("started_at", running_cutoff)
            .limit(200)
            .execute()
            .data
            or []
        )
        for row in stuck_running:
            run_id = row["run_id"]
            agent_name = row.get("agent_name", "")
            # HITL-aware gate: if hitl_expires_at is set and in the future, this
            # run is in the pre-interrupt window. Skip it — it will self-transition
            # to hitl_waiting when interrupt() fires. Kills it only once expiry passes.
            hitl_expires_at = row.get("hitl_expires_at")
            if hitl_expires_at:
                try:
                    expiry = datetime.fromisoformat(hitl_expires_at)
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    if expiry > now:
                        log.info(
                            "sweeper_skipping_pre_hitl_run",
                            run_id=run_id,
                            hitl_expires_at=hitl_expires_at,
                        )
                        continue
                except (ValueError, TypeError):
                    pass  # malformed timestamp — fall through to close
            try:
                _close_stuck(run_id, agent_name, reason="running_timeout")
                closed_running += 1
            except Exception as exc:  # noqa: BLE001 — log and continue
                log.warning(
                    "sweeper_close_failed",
                    run_id=run_id,
                    reason="running_timeout",
                    error=str(exc),
                )
    except Exception as exc:  # noqa: BLE001 — full cohort query failure
        log.error("sweeper_running_query_failed", error=str(exc))

    try:
        stuck_hitl = (
            supabase.table("agent_runs")
            .select("run_id,agent_name,created_at")
            .eq("status", "hitl_waiting")
            .lt("created_at", hitl_cutoff)
            .limit(200)
            .execute()
            .data
            or []
        )
        for row in stuck_hitl:
            run_id = row["run_id"]
            agent_name = row.get("agent_name", "")
            try:
                _close_stuck(run_id, agent_name, reason="hitl_sla_breach")
                closed_hitl += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "sweeper_close_failed",
                    run_id=run_id,
                    reason="hitl_sla_breach",
                    error=str(exc),
                )
    except Exception as exc:  # noqa: BLE001
        log.error("sweeper_hitl_query_failed", error=str(exc))

    if closed_running or closed_hitl:
        log.warning(
            "sweeper_stuck_runs_closed",
            running_timeouts=closed_running,
            hitl_sla_breaches=closed_hitl,
        )
        _alert_mission_control(
            f"⚠️ Sweeper closed {closed_running} timeout runs and "
            f"{closed_hitl} HITL SLA breaches"
        )

    return {"closed_running": closed_running, "closed_hitl": closed_hitl}


def _close_stuck(run_id: str, agent_name: str, *, reason: str) -> None:
    """Transition a stuck run to failed with sweeper provenance. Idempotent."""
    from omerion_core.runtime import run_lifecycle

    error_msg = f"sweeper:{reason} — closed by janitor at {_now_iso()}"
    run_lifecycle.fail_run(run_id, error=error_msg)
    # Mark superseded so any zombie ThreadPoolExecutor thread can't revive
    # the run via a late transition() call.
    run_lifecycle.mark_superseded(run_id)
    log.info("sweeper_run_closed", run_id=run_id, agent=agent_name, reason=reason)


# ─────────────────────────── HITL expiration ──────────────────────


def sweep_expired_hitl() -> dict[str, int]:
    """Mark `founder_review_queue` rows past expires_at as 'expired'."""
    cutoff_iso = _now_iso()
    expired_count = 0

    try:
        expired = (
            supabase.table("founder_review_queue")
            .select("review_id,agent_name,expires_at")
            .eq("decision", "pending")  # only sweep still-open reviews
            .lt("expires_at", cutoff_iso)
            .limit(200)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.error("sweeper_hitl_expired_query_failed", error=str(exc))
        return {"expired_count": 0}

    for row in expired:
        review_id = row["review_id"]
        try:
            supabase.table("founder_review_queue").update({
                "decision": "expired",
                "decided_at": cutoff_iso,
                "decision_notes": f"sweeper:expired_past_sla at {cutoff_iso}",
            }).eq("review_id", review_id).execute()
            expired_count += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "sweeper_hitl_expire_failed",
                review_id=review_id,
                error=str(exc),
            )

    if expired_count:
        log.warning("sweeper_hitl_expired", count=expired_count)
        _alert_mission_control(
            f"⏱️ Sweeper expired {expired_count} HITL review(s) past SLA"
        )

    return {"expired_count": expired_count}


# ─────────────────────────── DLQ replay ───────────────────────────


def sweep_dead_letter_queue() -> dict[str, int]:
    """Replay event_dead_letter rows that are due for retry.

    For each ready row, we re-emit the original event via the standard
    bus. If the emit succeeds, mark the DLQ row `delivered`. If it
    fails, bump `attempt_count` and schedule the next retry with
    exponential backoff. After `max_attempts`, the row is parked in
    `permanent_failure` for operator inspection.
    """
    now_iso = _now_iso()
    delivered = 0
    failed = 0
    abandoned = 0

    try:
        ready = (
            supabase.table("event_dead_letter")
            .select("*")
            .eq("status", "pending")
            .lte("next_retry_at", now_iso)
            .limit(50)  # bound each sweep tick
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.error("sweeper_dlq_query_failed", error=str(exc))
        return {"delivered": 0, "failed": 0, "abandoned": 0}

    for row in ready:
        dlq_id = row["dlq_id"]
        try:
            outcome = _replay_one(row)
        except Exception as exc:  # noqa: BLE001 — never let one row crash the sweep
            log.error("sweeper_dlq_replay_exception", dlq_id=dlq_id, error=str(exc))
            outcome = "transient"

        if outcome == "delivered":
            _mark_delivered(dlq_id)
            delivered += 1
            continue

        new_attempt = int(row.get("attempt_count", 0)) + 1
        if outcome == "unrecoverable":
            # Poison pill: park immediately, don't burn the retry budget.
            _mark_permanent_failure(dlq_id, attempt_count=new_attempt)
            abandoned += 1
            continue

        # transient: retry with backoff until max_attempts, then park.
        max_attempts = int(row.get("max_attempts", 5))
        if new_attempt >= max_attempts:
            _mark_permanent_failure(dlq_id, attempt_count=new_attempt)
            abandoned += 1
        else:
            _bump_attempt(dlq_id, attempt_count=new_attempt)
            failed += 1

    if delivered or failed or abandoned:
        log.info(
            "sweeper_dlq_tick",
            delivered=delivered,
            retried=failed,
            abandoned=abandoned,
        )
    if abandoned:
        _alert_mission_control(
            f"💀 Sweeper abandoned {abandoned} DLQ row(s) — operator review needed"
        )

    return {"delivered": delivered, "failed": failed, "abandoned": abandoned}


# Exceptions that mean the event will NEVER replay successfully (poison pill):
# a schema-invalid payload (bus raises ValueError), an unknown/typo event_type,
# or a structurally malformed payload. Retrying these just burns the attempt
# budget. pydantic.ValidationError is included explicitly (v2 ValidationError is
# NOT a subclass of ValueError).
_UNRECOVERABLE_REPLAY_ERRORS = (ValueError, TypeError, KeyError, ValidationError)


def _replay_one(row: dict[str, Any]) -> str:
    """Re-emit the original event via the canonical bus.

    Returns one of: "delivered" | "unrecoverable" | "transient". Poison-pill guard:
    an unrecoverable error (malformed/schema-invalid payload) is parked immediately
    by the caller rather than retried 5 times.
    """
    from omerion_core.events.bus import emit_event

    event_type = row.get("event_type", "")
    payload = row.get("payload") or {}
    source_agent = row.get("source_agent") or "sweeper.dlq"
    correlation_id = row.get("correlation_id")
    if not event_type:
        return "unrecoverable"  # a row with no event_type can never be re-emitted
    try:
        emit_event(
            event_type,
            source_agent=source_agent,
            payload=payload,
            correlation_id=correlation_id,
        )
        return "delivered"
    except _UNRECOVERABLE_REPLAY_ERRORS as exc:
        log.error("dlq_replay_unrecoverable", event_type=event_type, error=str(exc))
        return "unrecoverable"
    except Exception as exc:  # noqa: BLE001 — transient (network, DB availability)
        log.warning("dlq_replay_transient_failure", event_type=event_type, error=str(exc))
        return "transient"


def _mark_delivered(dlq_id: str) -> None:
    supabase.table("event_dead_letter").update({
        "status": "delivered",
        "delivered_at": _now_iso(),
        "updated_at": _now_iso(),
    }).eq("dlq_id", dlq_id).execute()


def _mark_permanent_failure(dlq_id: str, *, attempt_count: int) -> None:
    supabase.table("event_dead_letter").update({
        "status": "permanent_failure",
        "attempt_count": attempt_count,
        "updated_at": _now_iso(),
    }).eq("dlq_id", dlq_id).execute()


def _bump_attempt(dlq_id: str, *, attempt_count: int) -> None:
    delay_min = DLQ_RETRY_DELAYS_MINUTES[
        min(attempt_count - 1, len(DLQ_RETRY_DELAYS_MINUTES) - 1)
    ]
    next_retry = (datetime.now(timezone.utc) + timedelta(minutes=delay_min)).isoformat()
    supabase.table("event_dead_letter").update({
        "attempt_count": attempt_count,
        "next_retry_at": next_retry,
        "updated_at": _now_iso(),
    }).eq("dlq_id", dlq_id).execute()


# ─────────────────────────── notification outbox ──────────────────


NOTIFY_RETRY_DELAYS_MINUTES = (1, 3, 9, 27)  # matches retry_queue.py docstring


def sweep_notification_outbox() -> dict[str, int]:
    """Deliver pending `notification_outbox` rows (Wave 7.1).

    `notifications/retry_queue.enqueue()` writes rows with status='pending' but
    NOTHING drained them — Mission Control alerts (cost spike, error rate, stuck
    runs) and HITL notifications were enqueued and never sent. This is the missing
    drainer: POST each due row's payload to its `webhook_url`, mark `delivered`,
    or bump `attempt_count` with exponential backoff; park as `permanent_failure`
    after `max_attempts` and page the operator.

    Idempotent and fail-looping like the other sweeps.
    """
    now_iso = _now_iso()
    delivered = 0
    failed = 0
    abandoned = 0
    try:
        ready = (
            supabase.table("notification_outbox")
            .select("*")
            .eq("status", "pending")
            .lte("next_retry_at", now_iso)
            .limit(50)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.error("sweeper_outbox_query_failed", error=str(exc))
        return {"delivered": 0, "failed": 0, "abandoned": 0}

    for row in ready:
        outbox_id = row["outbox_id"]
        if _deliver_notification(row):
            _mark_outbox_delivered(outbox_id)
            delivered += 1
            continue
        new_attempt = int(row.get("attempt_count", 0)) + 1
        max_attempts = int(row.get("max_attempts", 4))
        if new_attempt >= max_attempts:
            _mark_outbox_permanent(outbox_id, attempt_count=new_attempt)
            abandoned += 1
        else:
            _bump_outbox_attempt(outbox_id, attempt_count=new_attempt)
            failed += 1

    if delivered or failed or abandoned:
        log.info("sweeper_outbox_tick", delivered=delivered, retried=failed, abandoned=abandoned)
    if abandoned:
        _alert_mission_control(
            f"💀 Sweeper abandoned {abandoned} notification(s) after max retries — operator review needed"
        )
    return {"delivered": delivered, "failed": failed, "abandoned": abandoned}


def _deliver_notification(row: dict[str, Any]) -> bool:
    """POST the row's payload to its webhook_url. Returns success."""
    url = row.get("webhook_url")
    payload = row.get("payload") or {}
    if not url:
        log.warning("outbox_row_missing_url", outbox_id=row.get("outbox_id"))
        return False
    try:
        import httpx

        with httpx.Client(timeout=5) as c:
            resp = c.post(url, json=payload)
        return resp.status_code < 400
    except Exception as exc:  # noqa: BLE001
        log.warning("outbox_delivery_failed", outbox_id=row.get("outbox_id"), error=str(exc))
        return False


def _mark_outbox_delivered(outbox_id: str) -> None:
    supabase.table("notification_outbox").update({
        "status": "delivered",
        "delivered_at": _now_iso(),
        "updated_at": _now_iso(),
    }).eq("outbox_id", outbox_id).execute()


def _mark_outbox_permanent(outbox_id: str, *, attempt_count: int) -> None:
    supabase.table("notification_outbox").update({
        "status": "permanent_failure",
        "attempt_count": attempt_count,
        "updated_at": _now_iso(),
    }).eq("outbox_id", outbox_id).execute()


def _bump_outbox_attempt(outbox_id: str, *, attempt_count: int) -> None:
    delay_min = NOTIFY_RETRY_DELAYS_MINUTES[
        min(attempt_count - 1, len(NOTIFY_RETRY_DELAYS_MINUTES) - 1)
    ]
    next_retry = (datetime.now(timezone.utc) + timedelta(minutes=delay_min)).isoformat()
    supabase.table("notification_outbox").update({
        "attempt_count": attempt_count,
        "next_retry_at": next_retry,
        "updated_at": _now_iso(),
    }).eq("outbox_id", outbox_id).execute()


# ─────────────────────────── alerts ───────────────────────────────


def _alert_mission_control(message: str) -> None:
    """Best-effort Discord webhook alert. Never raises."""
    import os

    import httpx
    from omerion_core.settings import settings

    url = (
        os.getenv("DISCORD_MISSION_CONTROL_WEBHOOK_URL")
        or getattr(settings, "discord_alerts_webhook_url", "")
        or ""
    )
    if not url:
        return
    try:
        with httpx.Client(timeout=5) as c:
            c.post(url, json={"content": message[:1900]})
    except Exception as exc:  # noqa: BLE001
        log.warning("sweeper_alert_failed", error=str(exc))


# ─────────────────────────── registration ─────────────────────────


def register_sweeper_jobs(scheduler: Any) -> None:
    """Register the three sweeper jobs into the APScheduler instance.

    Called from `omerion/main.py` lifespan() after `start_scheduler()`
    returns. The scheduler instance is `apscheduler.schedulers.background.BackgroundScheduler`.
    """
    try:
        scheduler.add_job(
            sweep_stuck_runs,
            trigger="interval",
            minutes=5,
            id="sweeper.stuck_runs",
            replace_existing=True,
            max_instances=1,
            coalesce=True,  # if we missed ticks during a restart, run just once
        )
        scheduler.add_job(
            sweep_expired_hitl,
            trigger="interval",
            minutes=5,
            id="sweeper.expired_hitl",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            sweep_dead_letter_queue,
            trigger="interval",
            minutes=10,
            id="sweeper.dlq",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            sweep_notification_outbox,
            trigger="interval",
            minutes=2,
            id="sweeper.notification_outbox",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info("sweeper_jobs_registered", count=4)
    except Exception as exc:  # noqa: BLE001
        log.error("sweeper_registration_failed", error=str(exc))


__all__ = [
    "sweep_stuck_runs",
    "sweep_expired_hitl",
    "sweep_dead_letter_queue",
    "sweep_notification_outbox",
    "register_sweeper_jobs",
]
