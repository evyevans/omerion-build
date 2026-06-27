"""LangGraph for SECURITY_AUDITOR.

Flow:
    scan_secrets        (deterministic: regex env/config scan)
      → scan_deps       (deterministic: pip-audit subprocess + CVE parse)
      → scan_endpoints  (deterministic: route grep for missing auth)
      → generate_brief  (LLM: weekly executive brief ONLY on report day)
      → emit_and_alert  (deterministic: Discord + Supabase + HITL on critical)

LLM justification: generate_brief synthesizes heterogeneous findings (secrets,
CVEs, endpoint exposure) into a threat narrative the founder can act on.
All detection logic is deterministic — the LLM never decides what IS a finding.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .prompts import SECURITY_BRIEF_SYSTEM, SECURITY_BRIEF_USER
from .state import SecurityAuditorState
from .tools import (
    persist_findings,
    scan_dependencies_for_cves,
    scan_env_for_secrets,
    scan_exposed_endpoints,
)

log = get_logger("omerion.agents.security_auditor")


@traced_node("scan_secrets")
def scan_secrets_node(state: SecurityAuditorState) -> SecurityAuditorState:
    """Node 1 — Regex scan for raw secrets in env files. Deterministic."""
    repo_root = getattr(settings, "repo_root", "/app")
    state.findings += scan_env_for_secrets(repo_root)
    log.info("security_secrets_scanned", secret_findings=sum(
        1 for f in state.findings if f.finding_type == "secret"
    ))
    return state


@traced_node("scan_deps")
def scan_deps_node(state: SecurityAuditorState) -> SecurityAuditorState:
    """Node 2 — pip-audit CVE scan. Deterministic."""
    repo_root = getattr(settings, "repo_root", "/app")
    state.findings += scan_dependencies_for_cves(repo_root)
    log.info("security_deps_scanned", cve_findings=sum(
        1 for f in state.findings if f.finding_type == "dependency_cve"
    ))
    return state


@traced_node("scan_endpoints")
def scan_endpoints_node(state: SecurityAuditorState) -> SecurityAuditorState:
    """Node 3 — Route auth coverage check. Deterministic grep."""
    repo_root = getattr(settings, "repo_root", "/app")
    state.findings += scan_exposed_endpoints(repo_root)
    state.critical_count = sum(1 for f in state.findings if f.severity == "critical")
    state.high_count = sum(1 for f in state.findings if f.severity == "high")
    log.info("security_endpoints_scanned", total_findings=len(state.findings))
    return state


@traced_node("generate_brief")
def generate_brief_node(state: SecurityAuditorState) -> SecurityAuditorState:
    """Node 4 — LLM executive brief. ONLY runs on weekly_report_day (Monday).

    LLM justified: translating mixed security findings (CVEs, secrets, endpoint
    exposure) into a prioritized, founder-readable narrative requires synthesis
    across finding types. All underlying detection was deterministic in Nodes 1-3.
    """
    today = date.today()
    if today.weekday() != state.weekly_report_day or not state.findings:
        log.info("security_brief_skipped", is_report_day=(today.weekday() == state.weekly_report_day))
        return state

    findings_block = "\n".join(
        f"  [{f.severity.upper()}] {f.finding_type}: {f.resource} — {f.description[:150]}"
        for f in state.findings
    )

    router = ClaudeRouter()
    resp = router.complete(
        tier=Tier.DEFAULT,
        system=SECURITY_BRIEF_SYSTEM,
        prompt=SECURITY_BRIEF_USER.format(
            total=len(state.findings),
            critical_count=state.critical_count,
            high_count=state.high_count,
            other_count=len(state.findings) - state.critical_count - state.high_count,
            findings_block=findings_block[:4000],
        ),
        max_tokens=1024,
        temperature=0.1,
        agent_name=state.agent_name,
        run_id=str(state.run_id),
        correlation_id=str(state.correlation_id) if state.correlation_id else None,
    )
    state.record_llm(resp["usage"], resp["cost_usd"])
    state.security_brief_md = resp["text"].strip()
    log.info("security_brief_generated")
    return state


@traced_node("emit_and_alert")
def emit_and_alert_node(state: SecurityAuditorState) -> SecurityAuditorState:
    """Node 5 — Persist findings, HITL on critical, emit event."""
    persist_findings(str(state.run_id), state.findings)

    for finding in state.findings:
        if finding.severity == "critical":
            try:
                create_founder_review_task(
                    agent_name=state.agent_name,
                    session_id=state.session_id,
                    subject=f"SECURITY: {finding.finding_type} — {finding.resource}",
                    context_md=(
                        f"**{finding.description}**\n\n"
                        f"Remediation: {finding.remediation or 'See finding details.'}"
                    ),
                    draft_ref={"run_id": str(state.run_id), "resource": finding.resource},
                    correlation_id=state.correlation_id,
                )
            except Exception as exc:
                log.warning("security_hitl_create_failed", resource=finding.resource, error=str(exc))

    if state.security_brief_md:
        try:
            create_founder_review_task(
                agent_name=state.agent_name,
                session_id=state.session_id,
                subject=f"SECURITY Weekly Brief — {date.today().isoformat()}",
                context_md=state.security_brief_md[:4000],
                draft_ref={"kind": "security_weekly_brief"},
                correlation_id=state.correlation_id,
            )
        except Exception as exc:
            log.warning("security_weekly_brief_hitl_failed", error=str(exc))

    state.verdict = "critical_found" if state.critical_count > 0 else "passed"

    event_type = (
        EventType.SECURITY_VIOLATION_DETECTED
        if state.critical_count > 0
        else EventType.SECURITY_SCAN_PASSED
    )
    emit_event(
        event_type,
        source_agent=state.agent_name,
        payload={
            "total_findings": len(state.findings),
            "critical_count": state.critical_count,
            "high_count": state.high_count,
            "run_id": str(state.run_id),
        },
        correlation_id=state.correlation_id,
    )
    log.info("security_emit_complete", verdict=state.verdict)
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(SecurityAuditorState)
    g.add_node("scan_secrets", scan_secrets_node)
    g.add_node("scan_deps", scan_deps_node)
    g.add_node("scan_endpoints", scan_endpoints_node)
    g.add_node("generate_brief", generate_brief_node)
    g.add_node("emit_and_alert", emit_and_alert_node)

    g.set_entry_point("scan_secrets")
    g.add_edge("scan_secrets", "scan_deps")
    g.add_edge("scan_deps", "scan_endpoints")
    g.add_edge("scan_endpoints", "generate_brief")
    g.add_edge("generate_brief", "emit_and_alert")
    g.add_edge("emit_and_alert", END)

    return g.compile(checkpointer=get_checkpointer())
