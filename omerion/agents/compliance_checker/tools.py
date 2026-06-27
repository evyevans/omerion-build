"""Deterministic compliance rule checks for COMPLIANCE_CHECKER.

Every function here is a pure predicate — no LLM calls.
The LLM is used ONLY in graph.py for the weekly trend narrative.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

from .state import ComplianceViolation

log = get_logger("omerion.agents.compliance_checker")

# ─── Rule CC-1: COST_CAP_COMPLIANCE ──────────────────────────────────────────

def check_cost_caps(agent_names: list[str], window_hours: int = 24) -> list[ComplianceViolation]:
    """Rule CC-1: Agent runs must not exceed per_run_cost_cap_usd.

    Deterministic: SQL aggregation + arithmetic comparison.
    """
    violations: list[ComplianceViolation] = []
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    for agent_name in agent_names:
        try:
            cap_resp = (
                supabase.table("agent_config")
                .select("per_run_cost_cap_usd")
                .eq("agent_name", agent_name)
                .limit(1)
                .execute()
            )
            if not cap_resp.data:
                continue
            cap = float(cap_resp.data[0].get("per_run_cost_cap_usd") or 0)
            if cap <= 0:
                continue

            runs_resp = (
                supabase.table("agent_runs")
                .select("run_id, cost_usd")
                .eq("agent_name", agent_name)
                .gte("created_at", since)
                .execute()
            )
            for run in (runs_resp.data or []):
                actual = float(run.get("cost_usd") or 0)
                if actual > cap:
                    violations.append(ComplianceViolation(
                        rule_id="CC-1:COST_CAP",
                        severity="critical",
                        target_agent=agent_name,
                        description=f"{agent_name} run {run['run_id']} cost ${actual:.4f}, cap is ${cap:.4f}",
                    ))
        except Exception as exc:
            log.warning("compliance_cost_check_error", agent=agent_name, error=str(exc))

    return violations


# ─── Rule CC-2: DATA_RETENTION ────────────────────────────────────────────────

def check_data_retention(retention_days: int = 90) -> list[ComplianceViolation]:
    """Rule CC-2: Contact PII must not exceed retention_days in the DB.

    Deterministic: SQL date arithmetic.
    """
    violations: list[ComplianceViolation] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

    try:
        resp = (
            supabase.table("contacts")
            .select("contact_id, created_at")
            .lt("created_at", cutoff)
            .limit(10)
            .execute()
        )
        if resp.data:
            violations.append(ComplianceViolation(
                rule_id="CC-2:DATA_RETENTION",
                severity="warning",
                target_agent=None,
                description=(
                    f"{len(resp.data)} contact record(s) older than {retention_days} days. "
                    "Review for anonymization or deletion per data retention policy."
                ),
            ))
    except Exception as exc:
        log.warning("compliance_retention_check_error", error=str(exc))

    return violations


# ─── Rule CC-3: API_WHITELIST ─────────────────────────────────────────────────

_APPROVED_HOSTS: frozenset[str] = frozenset([
    "api.anthropic.com", "api.pinecone.io", "api.github.com",
    "discord.com", "hooks.slack.com", "api.stripe.com",
    "firecrawl.dev", "hunter.io", "serpapi.com",
])


def check_api_whitelist(window_hours: int = 24) -> list[ComplianceViolation]:
    """Rule CC-3: Outbound API calls must only target approved hosts.

    Deterministic: string membership in the approved frozenset.
    """
    violations: list[ComplianceViolation] = []
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()

    try:
        resp = (
            supabase.table("api_call_log")
            .select("host, agent_name, called_at")
            .gte("called_at", since)
            .execute()
        )
        seen_hosts: set[str] = set()
        for row in (resp.data or []):
            host = (row.get("host") or "").lower().strip()
            if host and host not in _APPROVED_HOSTS and host not in seen_hosts:
                seen_hosts.add(host)
                violations.append(ComplianceViolation(
                    rule_id="CC-3:API_WHITELIST",
                    severity="critical",
                    target_agent=row.get("agent_name"),
                    description=f"Unapproved API host detected: {host}",
                ))
    except Exception as exc:
        log.warning("compliance_api_whitelist_check_error", error=str(exc))

    return violations


def persist_violations(run_id: str, violations: list[ComplianceViolation]) -> int:
    """Insert violations into compliance_violations table. Returns count inserted."""
    if not violations:
        return 0
    try:
        rows = [
            {
                "run_id": run_id,
                "rule_id": v.rule_id,
                "severity": v.severity,
                "target_agent": v.target_agent,
                "description": v.description,
            }
            for v in violations
        ]
        supabase.table("compliance_violations").insert(rows).execute()
        return len(rows)
    except Exception as exc:
        log.warning("compliance_persist_failed", error=str(exc))
        return 0


def fetch_recent_violations(days: int = 7) -> list[dict]:
    """Load last N days of violations for trend analysis."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        resp = (
            supabase.table("compliance_violations")
            .select("*")
            .gte("created_at", since)
            .order("created_at", desc=True)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning("compliance_fetch_violations_failed", error=str(exc))
        return []
