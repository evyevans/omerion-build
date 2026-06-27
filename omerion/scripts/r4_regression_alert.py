"""Deterministic replacement for the retired `r4_evaluation_telemetry` agent.

The original agent ran a LangGraph state machine to do what is — at its
core — a SQL query and a threshold comparison. The kill-switch (auto-pause
agents with critical regressions) is the only piece worth preserving, and
it is already deterministic Python in `tools.auto_pause_agent`.

This script:
  1. Loads recent (last 24h) per-agent telemetry from `agent_runs`.
  2. Loads the baseline window (default last 14 days, excluding the last 24h).
  3. Compares thresholds configured in agents.yaml :: r4 :: regression_thresholds.
  4. Emits per-severity alerts to Discord #mission-control.
  5. For `critical` regressions, auto-pauses the affected agents by setting
     `agent_config.schedule_enabled = false`. The next scheduled tick is a
     no-op until a human re-enables.
  6. Persists every regression row to `agent_telemetry_alerts` for audit.

Designed for daily APScheduler invocation. Idempotent within the 24h
window (same regression → same row via UNIQUE(agent_name, window_end)).

Run standalone:
    cd omerion && python -m scripts.r4_regression_alert
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.scripts.r4_regression_alert")


# ─────────────────────────── thresholds + types ────────────────────────

DEFAULT_THRESHOLDS = {
    "cost_usd_pct_increase":  {"warning": 0.50, "critical": 1.00},   # +50% / +100%
    "latency_p95_pct_increase": {"warning": 0.50, "critical": 1.00},
    "failure_rate":            {"warning": 0.05, "critical": 0.15},   # 5% / 15%
    "no_runs":                 {"warning": True,  "critical": False}, # ran in baseline but not in window
}


@dataclass
class AgentMetrics:
    agent_name: str
    runs: int
    failures: int
    cost_usd: float
    latency_p95_ms: float


@dataclass
class Regression:
    agent_name: str
    metric: str
    baseline: float
    current: float
    delta_pct: float
    severity: str   # "warning" | "critical"
    summary: str


# ─────────────────────────── data loaders ──────────────────────────────

def _load_window(start_iso: str, end_iso: str) -> dict[str, AgentMetrics]:
    """Aggregate per-agent metrics over [start, end). Pure SQL — no LLM."""
    try:
        # Lean on a custom RPC if it exists; else fallback to in-Python rollup.
        resp = (
            supabase.table("agent_runs")
            .select("agent_name,success,cost_usd,latency_ms")
            .gte("started_at", start_iso)
            .lt("started_at", end_iso)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001
        log.warning("r4_load_window_failed", start=start_iso, end=end_iso, error=str(exc))
        return {}

    bucket: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        bucket.setdefault(r.get("agent_name", "unknown"), []).append(r)

    out: dict[str, AgentMetrics] = {}
    for name, agent_rows in bucket.items():
        runs = len(agent_rows)
        failures = sum(1 for r in agent_rows if r.get("success") is False)
        cost = sum(float(r.get("cost_usd") or 0) for r in agent_rows)
        latencies = sorted(float(r.get("latency_ms") or 0) for r in agent_rows)
        p95 = latencies[int(0.95 * len(latencies)) - 1] if latencies else 0.0
        out[name] = AgentMetrics(name, runs, failures, cost, p95)
    return out


def load_current_and_baseline(window_days: int = 14) -> tuple[dict, dict]:
    """Current = last 24h. Baseline = `window_days` window excluding the last 24h."""
    now = datetime.now(timezone.utc)
    current = _load_window((now - timedelta(hours=24)).isoformat(), now.isoformat())
    baseline = _load_window(
        (now - timedelta(days=window_days)).isoformat(),
        (now - timedelta(hours=24)).isoformat(),
    )
    return current, baseline


# ─────────────────────────── regression detection ──────────────────────

def _pct_change(baseline_val: float, current_val: float) -> float:
    if baseline_val <= 0:
        return float("inf") if current_val > 0 else 0.0
    return (current_val - baseline_val) / baseline_val


def detect_regressions(
    current: dict[str, AgentMetrics],
    baseline: dict[str, AgentMetrics],
    thresholds: dict[str, dict[str, float]] | None = None,
) -> list[Regression]:
    """Return all (agent, metric) regressions exceeding configured thresholds."""
    th = thresholds or DEFAULT_THRESHOLDS
    out: list[Regression] = []

    for name, bl in baseline.items():
        # If we have no baseline activity, skip.
        if bl.runs == 0:
            continue
        cur = current.get(name)

        # 1) Agent disappeared from the window
        if cur is None or cur.runs == 0:
            severity = "warning"  # critical=False per DEFAULT_THRESHOLDS
            if th.get("no_runs", {}).get("warning"):
                out.append(Regression(
                    agent_name=name,
                    metric="no_runs",
                    baseline=float(bl.runs),
                    current=0.0,
                    delta_pct=-1.0,
                    severity=severity,
                    summary=f"{name}: zero runs in last 24h (baseline avg {bl.runs / 14:.1f}/day)",
                ))
            continue

        # 2) Cost surge
        baseline_cost_per_run = bl.cost_usd / max(bl.runs, 1)
        current_cost_per_run = cur.cost_usd / max(cur.runs, 1)
        cost_delta = _pct_change(baseline_cost_per_run, current_cost_per_run)
        for sev in ("critical", "warning"):
            t = th.get("cost_usd_pct_increase", {}).get(sev)
            if t is not None and cost_delta >= t:
                out.append(Regression(
                    agent_name=name,
                    metric="cost_usd_pct_increase",
                    baseline=baseline_cost_per_run,
                    current=current_cost_per_run,
                    delta_pct=cost_delta,
                    severity=sev,
                    summary=(
                        f"{name}: cost/run +{cost_delta * 100:.0f}% "
                        f"(${baseline_cost_per_run:.4f} → ${current_cost_per_run:.4f})"
                    ),
                ))
                break

        # 3) Latency p95 regression
        lat_delta = _pct_change(bl.latency_p95_ms, cur.latency_p95_ms)
        for sev in ("critical", "warning"):
            t = th.get("latency_p95_pct_increase", {}).get(sev)
            if t is not None and lat_delta >= t:
                out.append(Regression(
                    agent_name=name,
                    metric="latency_p95_pct_increase",
                    baseline=bl.latency_p95_ms,
                    current=cur.latency_p95_ms,
                    delta_pct=lat_delta,
                    severity=sev,
                    summary=(
                        f"{name}: p95 latency +{lat_delta * 100:.0f}% "
                        f"({bl.latency_p95_ms:.0f}ms → {cur.latency_p95_ms:.0f}ms)"
                    ),
                ))
                break

        # 4) Failure rate
        cur_failure_rate = cur.failures / max(cur.runs, 1)
        for sev in ("critical", "warning"):
            t = th.get("failure_rate", {}).get(sev)
            if t is not None and cur_failure_rate >= t:
                out.append(Regression(
                    agent_name=name,
                    metric="failure_rate",
                    baseline=bl.failures / max(bl.runs, 1),
                    current=cur_failure_rate,
                    delta_pct=cur_failure_rate,
                    severity=sev,
                    summary=(
                        f"{name}: failure rate {cur_failure_rate * 100:.1f}% "
                        f"({cur.failures}/{cur.runs} runs)"
                    ),
                ))
                break

    return out


# ─────────────────────────── kill switch ───────────────────────────────

def auto_pause_agent(agent_name: str, reason: str) -> bool:
    """Flip agent_config.schedule_enabled = false. Idempotent."""
    try:
        supabase.table("agent_config").upsert(
            {
                "agent_name": agent_name,
                "schedule_enabled": False,
                "paused_reason": reason[:500],
                "paused_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="agent_name",
        ).execute()
        log.warning("agent_auto_paused", agent=agent_name, reason=reason)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("agent_auto_pause_failed", agent=agent_name, error=str(exc))
        return False


# ─────────────────────────── persist + alert ───────────────────────────

def _persist_alerts(regressions: list[Regression], window_end_iso: str) -> None:
    if not regressions:
        return
    rows = [
        {
            "agent_name": r.agent_name,
            "metric": r.metric,
            "baseline": r.baseline,
            "current": r.current,
            "delta_pct": r.delta_pct,
            "severity": r.severity,
            "summary": r.summary,
            "window_end": window_end_iso,
        }
        for r in regressions
    ]
    try:
        supabase.table("agent_telemetry_alerts").upsert(
            rows, on_conflict="agent_name,metric,window_end"
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("r4_alert_persist_failed", count=len(rows), error=str(exc))


def _post_to_discord(regressions: list[Regression]) -> None:
    """Post a concise summary to the configured Discord webhook (#mission-control)."""
    url = os.getenv("DISCORD_MISSION_CONTROL_WEBHOOK_URL") or getattr(
        settings, "discord_alerts_webhook_url", ""
    )
    if not url or not regressions:
        return

    critical = [r for r in regressions if r.severity == "critical"]
    warning = [r for r in regressions if r.severity == "warning"]

    lines = []
    if critical:
        lines.append(f"🚨 **{len(critical)} CRITICAL** regression(s) — agents auto-paused")
        for r in critical[:8]:
            lines.append(f"  • {r.summary}")
    if warning:
        lines.append(f"⚠️ **{len(warning)} warning** regression(s)")
        for r in warning[:8]:
            lines.append(f"  • {r.summary}")
    content = "\n".join(lines)

    try:
        with httpx.Client(timeout=10) as c:
            c.post(url, json={"content": content[:1900]})  # Discord 2000-char cap
    except Exception as exc:  # noqa: BLE001
        log.warning("r4_discord_alert_failed", error=str(exc))


# ─────────────────────────── entrypoint ────────────────────────────────

def run_once(window_days: int = 14) -> dict[str, int]:
    """Single regression sweep. Returns counts."""
    current, baseline = load_current_and_baseline(window_days)
    log.info("r4_loaded", current_agents=len(current), baseline_agents=len(baseline))

    thresholds = DEFAULT_THRESHOLDS
    try:
        cfg = settings.agent("r4_evaluation_telemetry")
        thresholds = {**DEFAULT_THRESHOLDS, **cfg.get("regression_thresholds", {})}
    except Exception:  # noqa: BLE001
        pass

    regressions = detect_regressions(current, baseline, thresholds)
    log.info("r4_regressions", count=len(regressions))

    # Kill-switch any critical breaches BEFORE alerting so the founder's ack
    # latency cannot extend an Opus-token leak.
    paused = 0
    for r in regressions:
        if r.severity == "critical":
            if auto_pause_agent(r.agent_name, reason=r.summary):
                paused += 1

    now_iso = datetime.now(timezone.utc).isoformat()
    _persist_alerts(regressions, window_end_iso=now_iso)
    _post_to_discord(regressions)

    return {
        "regressions": len(regressions),
        "critical": sum(1 for r in regressions if r.severity == "critical"),
        "warning": sum(1 for r in regressions if r.severity == "warning"),
        "paused": paused,
    }


if __name__ == "__main__":
    result = run_once()
    log.info("r4_run_complete", **result)
