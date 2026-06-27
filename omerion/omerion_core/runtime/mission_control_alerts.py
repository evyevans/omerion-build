"""Mission Control alert engine (Wave 3.5).

Four alert classes, all designed to be cheap (single SQL aggregate
queries) and idempotent at the day-bucket level (sweeper runs every 5
minutes; we don't want to spam the same alert).

  1. **Cost spike** — 1h spend > 2× rolling 7-day hourly average.
  2. **Error rate** — errors / runs > 5% over the last 1 hour.
  3. **Stuck runs** — any agent_runs in `running` past timeout, or
     `hitl_waiting` past SLA (the sweeper closes these, this alert
     fires when the *backlog* of stuck runs exceeds a threshold even
     after sweeping).
  4. **HITL backlog** — count of `pending` founder_review_queue rows
     above a configurable threshold (default 10).

Each alert is dedup-keyed at the (alert_type, day, hour) level via the
existing `notification_outbox` retry queue — same alert in the same
hour is a no-op insert.

Designed to be invoked from APScheduler (registered in
`sweeper.register_sweeper_jobs` extension) on a 15-minute interval.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.notifications.retry_queue import enqueue
from omerion_core.settings import settings

log = get_logger("omerion.mission_control.alerts")


# Thresholds. Wave 3.5 hardcoded; can move to settings.py later.
COST_SPIKE_MULTIPLIER         = 2.0     # 1h spend > 2× 7d hourly avg
ERROR_RATE_THRESHOLD          = 0.05    # 5% errors / runs over 1h
HITL_BACKLOG_THRESHOLD        = 10      # pending reviews
STUCK_RUNS_BACKLOG_THRESHOLD  = 5       # post-sweeper residual


def _alert_webhook_url() -> str | None:
    import os
    return (
        os.getenv("DISCORD_MISSION_CONTROL_WEBHOOK_URL")
        or getattr(settings, "discord_alerts_webhook_url", "")
        or None
    )


def _hour_bucket(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H")


def _enqueue_alert(alert_type: str, message: str) -> None:
    """Hour-bucketed enqueue. Same alert_type in the same hour is a no-op."""
    url = _alert_webhook_url()
    if not url:
        log.info("alert_skipped_no_webhook", alert_type=alert_type)
        return
    target = f"{alert_type}:{_hour_bucket()}"
    enqueue(
        notification_type="cost_spike" if alert_type.startswith("cost") else "error_alert",
        target_id=target,
        webhook_url=url,
        payload={"content": message[:1900]},
    )


def send_alert(alert_type: str, message: str) -> None:
    """Public, durable founder alert. Routes through the notification_outbox
    (delivered by `sweeper.sweep_notification_outbox`). Hour-bucketed dedupe on
    `alert_type`. Use for event-driven operator notifications that the periodic
    health checks below don't already cover (e.g. a failed deployment). Never
    raises — alerting must not break the caller's run.
    """
    try:
        _enqueue_alert(alert_type, message)
    except Exception as exc:  # noqa: BLE001
        log.warning("send_alert_failed", alert_type=alert_type, error=str(exc))


# ─────────────────────────── checks ───────────────────────────────


def check_cost_spike() -> bool:
    """Return True iff last-hour spend exceeds 2× 7d hourly average.

    Two queries:
      1. SUM(cost_usd) where started_at >= now - 1h
      2. SUM(cost_usd) / (24*7) where started_at between 7d and 1h ago
    """
    now = datetime.now(timezone.utc)
    last_hour = (now - timedelta(hours=1)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()
    try:
        recent = (
            supabase.table("agent_runs")
            .select("cost_usd")
            .gte("started_at", last_hour)
            .execute()
            .data
            or []
        )
        baseline = (
            supabase.table("agent_runs")
            .select("cost_usd")
            .gte("started_at", week_ago)
            .lt("started_at", last_hour)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alert_cost_spike_query_failed", error=str(exc))
        return False

    recent_total = sum(float(r.get("cost_usd") or 0) for r in recent)
    baseline_total = sum(float(r.get("cost_usd") or 0) for r in baseline)
    baseline_hourly_avg = baseline_total / (24 * 7) if baseline_total else 0.0

    if baseline_hourly_avg <= 0:
        return False  # no baseline yet — don't alert on bootstrap noise
    if recent_total > baseline_hourly_avg * COST_SPIKE_MULTIPLIER:
        _enqueue_alert(
            "cost_spike",
            (
                f"💸 **Cost spike** — last hour spent ${recent_total:.2f} "
                f"(7d hourly avg ${baseline_hourly_avg:.2f}, "
                f"multiplier ×{COST_SPIKE_MULTIPLIER})"
            ),
        )
        log.warning(
            "alert_cost_spike", recent=recent_total, baseline_hourly_avg=baseline_hourly_avg
        )
        return True
    return False


def check_error_rate() -> bool:
    """Return True iff last-hour errors/runs > 5%."""
    last_hour = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    try:
        rows = (
            supabase.table("agent_runs")
            .select("status")
            .gte("started_at", last_hour)
            .limit(2000)
            .execute()
            .data
            or []
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alert_error_rate_query_failed", error=str(exc))
        return False

    total = len(rows)
    if total < 10:
        return False  # too noisy to alert on
    failed = sum(1 for r in rows if r.get("status") == "failed")
    rate = failed / total
    if rate > ERROR_RATE_THRESHOLD:
        _enqueue_alert(
            "error_rate",
            (
                f"❌ **Error rate** — {rate * 100:.1f}% over the last hour "
                f"({failed}/{total} runs failed)"
            ),
        )
        log.warning("alert_error_rate", rate=rate, failed=failed, total=total)
        return True
    return False


def check_hitl_backlog() -> bool:
    """Return True iff pending founder_review_queue count > threshold."""
    try:
        resp = (
            supabase.table("founder_review_queue")
            .select("review_id", count="exact")
            .eq("decision", "pending")
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alert_hitl_backlog_query_failed", error=str(exc))
        return False

    count = int(resp.count or 0) if hasattr(resp, "count") else len(resp.data or [])
    if count > HITL_BACKLOG_THRESHOLD:
        _enqueue_alert(
            "hitl_backlog",
            f"📋 **HITL backlog** — {count} pending founder reviews (threshold {HITL_BACKLOG_THRESHOLD})",
        )
        log.warning("alert_hitl_backlog", count=count)
        return True
    return False


def check_stuck_runs() -> bool:
    """Return True iff stuck-run backlog exceeds threshold after sweep.

    The sweeper closes stuck runs every 5 minutes — but if the *rate*
    of stuck runs is high enough that they accumulate between sweeps,
    we want a separate alert.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    try:
        resp = (
            supabase.table("agent_runs")
            .select("run_id", count="exact")
            .in_("status", ["running", "hitl_waiting"])
            .lt("started_at", cutoff)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("alert_stuck_runs_query_failed", error=str(exc))
        return False

    count = int(resp.count or 0) if hasattr(resp, "count") else len(resp.data or [])
    if count > STUCK_RUNS_BACKLOG_THRESHOLD:
        _enqueue_alert(
            "stuck_runs",
            f"🐌 **Stuck-run backlog** — {count} runs past timeout (threshold {STUCK_RUNS_BACKLOG_THRESHOLD})",
        )
        log.warning("alert_stuck_runs", count=count)
        return True
    return False


# ─────────────────────────── orchestrator ─────────────────────────


def run_all_checks() -> dict[str, bool]:
    """Run every alert check. Designed for the APScheduler 15-min tick."""
    return {
        "cost_spike":   check_cost_spike(),
        "error_rate":   check_error_rate(),
        "hitl_backlog": check_hitl_backlog(),
        "stuck_runs":   check_stuck_runs(),
    }


def register_alert_jobs(scheduler: Any) -> None:
    """Register the 15-min alert sweep on the existing APScheduler."""
    try:
        scheduler.add_job(
            run_all_checks,
            trigger="interval",
            minutes=15,
            id="mission_control.alerts",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info("mission_control_alerts_registered")
    except Exception as exc:  # noqa: BLE001
        log.error("mission_control_alerts_registration_failed", error=str(exc))


__all__ = [
    "check_cost_spike",
    "check_error_rate",
    "check_hitl_backlog",
    "check_stuck_runs",
    "run_all_checks",
    "register_alert_jobs",
]
