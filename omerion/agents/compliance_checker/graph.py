"""LangGraph for COMPLIANCE_CHECKER.

Flow:
    fetch_targets           (deterministic: agent list from config)
      → run_checks          (deterministic: CC-1, CC-2, CC-3 predicates)
      → trend_analysis      (LLM: weekly report ONLY on Mondays — justified)
      → notify_persist      (deterministic: Supabase + HITL on critical)
      → emit                (COMPLIANCE_SWEEP_COMPLETE / COMPLIANCE_VIOLATION_DETECTED)

LLM justification: trend_analysis synthesizes cross-agent violation patterns
across 7 days into a human-readable narrative. This cannot be expressed as a
predicate. All individual rule checks (CC-1/2/3) remain pure Python.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .prompts import COMPLIANCE_TREND_SYSTEM, COMPLIANCE_TREND_USER
from .state import ComplianceCheckerState
from .tools import (
    check_api_whitelist,
    check_cost_caps,
    check_data_retention,
    fetch_recent_violations,
    persist_violations,
)

log = get_logger("omerion.agents.compliance_checker")

_TARGET_AGENTS = [
    "crm_nurture", "linkedin_outreach", "offer_matching",
    "meeting_intelligence", "lead_scraper_enricher", "outcome_attribution",
    "biz_dev_outreach", "r1_market_tech_watcher",
]


@traced_node("fetch_targets")
def fetch_targets_node(state: ComplianceCheckerState) -> ComplianceCheckerState:
    """Node 1 — Resolve agent list. Deterministic."""
    state.agent_names = _TARGET_AGENTS
    log.info("compliance_checker_targets_loaded", count=len(state.agent_names))
    return state


@traced_node("run_checks")
def run_checks_node(state: ComplianceCheckerState) -> ComplianceCheckerState:
    """Node 2 — Execute all three deterministic compliance predicates."""
    all_violations = []
    all_violations += check_cost_caps(state.agent_names, state.scan_window_hours)
    all_violations += check_data_retention()
    all_violations += check_api_whitelist(state.scan_window_hours)

    state.violations = all_violations
    state.critical_count = sum(1 for v in all_violations if v.severity == "critical")
    state.warning_count = sum(1 for v in all_violations if v.severity == "warning")

    log.info(
        "compliance_checks_complete",
        total=len(all_violations),
        critical=state.critical_count,
        warnings=state.warning_count,
    )
    return state


@traced_node("trend_analysis")
def trend_analysis_node(state: ComplianceCheckerState) -> ComplianceCheckerState:
    """Node 3 — LLM trend report. ONLY runs on weekly_report_day (Monday).

    LLM justified: synthesizing cross-agent violation patterns across 7 days
    into a narrative trend report cannot be expressed as a deterministic predicate.
    This is purely a reporting function — it never makes a compliance decision.
    """
    today = date.today()
    if today.weekday() != state.weekly_report_day:
        log.info("compliance_trend_skipped_not_report_day")
        return state

    recent = fetch_recent_violations(days=7)
    if not recent:
        state.trend_report_md = "(No violations in the past 7 days — clean sweep.)"
        return state

    rule_counts: Counter = Counter(r.get("rule_id") for r in recent)
    agent_counts: Counter = Counter(r.get("target_agent") for r in recent if r.get("target_agent"))

    rules_block = "\n".join(f"- {rule}: {cnt}" for rule, cnt in rule_counts.most_common())
    agents_block = "\n".join(f"- {agent}: {cnt}" for agent, cnt in agent_counts.most_common(5))

    router = ClaudeRouter()
    resp = router.complete(
        tier=Tier.DEFAULT,
        system=COMPLIANCE_TREND_SYSTEM,
        prompt=COMPLIANCE_TREND_USER.format(
            window_days=7,
            total=len(recent),
            critical_count=sum(1 for r in recent if r.get("severity") == "critical"),
            warning_count=sum(1 for r in recent if r.get("severity") == "warning"),
            rules_block=rules_block,
            agents_block=agents_block,
        ),
        max_tokens=1024,
        temperature=0.2,
        agent_name=state.agent_name,
        run_id=str(state.run_id),
        correlation_id=str(state.correlation_id) if state.correlation_id else None,
    )
    state.record_llm(resp["usage"], resp["cost_usd"])
    state.trend_report_md = resp["text"].strip()
    log.info("compliance_trend_report_generated")
    return state


@traced_node("notify_persist")
def notify_persist_node(state: ComplianceCheckerState) -> ComplianceCheckerState:
    """Node 4 — Persist violations + create HITL for critical ones."""
    persist_violations(str(state.run_id), state.violations)

    for v in state.violations:
        if v.severity == "critical":
            try:
                create_founder_review_task(
                    agent_name=state.agent_name,
                    session_id=state.session_id,
                    subject=f"COMPLIANCE: {v.rule_id} — {v.target_agent or 'fleet'}",
                    context_md=v.description,
                    draft_ref={"rule_id": v.rule_id, "run_id": str(state.run_id)},
                    correlation_id=state.correlation_id,
                )
            except Exception as exc:
                log.warning("compliance_hitl_create_failed", rule=v.rule_id, error=str(exc))

    if state.trend_report_md:
        try:
            create_founder_review_task(
                agent_name=state.agent_name,
                session_id=state.session_id,
                subject=f"COMPLIANCE Weekly Trend Report — {date.today().isoformat()}",
                context_md=state.trend_report_md[:4000],
                draft_ref={"kind": "compliance_weekly_trend"},
                correlation_id=state.correlation_id,
            )
        except Exception as exc:
            log.warning("compliance_weekly_hitl_failed", error=str(exc))

    state.verdict = "violations_found" if state.violations else "clean"
    log.info("compliance_notify_persist_complete", verdict=state.verdict)
    return state


@traced_node("emit")
def emit_node(state: ComplianceCheckerState) -> ComplianceCheckerState:
    """Node 5 — Emit terminal event."""
    event_type = (
        EventType.COMPLIANCE_VIOLATION_DETECTED
        if state.critical_count > 0
        else EventType.COMPLIANCE_SWEEP_COMPLETE
    )
    emit_event(
        event_type,
        source_agent=state.agent_name,
        payload={
            "total_violations": len(state.violations),
            "critical_count": state.critical_count,
            "warning_count": state.warning_count,
            "run_id": str(state.run_id),
        },
        correlation_id=state.correlation_id,
    )
    log.info("compliance_emit_complete", emitted_event=event_type.value)
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(ComplianceCheckerState)
    g.add_node("fetch_targets", fetch_targets_node)
    g.add_node("run_checks", run_checks_node)
    g.add_node("trend_analysis", trend_analysis_node)
    g.add_node("notify_persist", notify_persist_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("fetch_targets")
    g.add_edge("fetch_targets", "run_checks")
    g.add_edge("run_checks", "trend_analysis")
    g.add_edge("trend_analysis", "notify_persist")
    g.add_edge("notify_persist", "emit")
    g.add_edge("emit", END)

    return g.compile(checkpointer=get_checkpointer())
