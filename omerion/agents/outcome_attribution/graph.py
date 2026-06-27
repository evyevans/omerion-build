"""LangGraph for Outcome Attribution & Feedback (Agent #10).

Trigger:
    - Cron (daily) — fans out one session per deployment whose go_live_date
      falls at least `window_days` in the past.
    - Realtime — `deployment.live` event schedules a session to fire once
      `window_days` have elapsed.

Flow:
    load_deployment
      → compute_deltas        (KPI pre/post from agent_telemetry + revenue + conversions)
      → summarize             (Claude Sonnet — founder-facing markdown)
      → feedback              (Claude Sonnet — structured JSON recommendations)
      → persist_report        (attribution_reports row)
      → persist_feedback      (generated_drafts rows, one per recommendation)
      → emit                  (attribution.report.ready + rd.insights.batch.ready)
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .state import AttributionState
from .tools import (
    _window,
    compute_deltas,
    conversion_rate,
    derive_proof_point,
    embed_outcome,
    generate_feedback,
    load_deployment,
    persona_for,
    render_summary,
    sum_revenue,
    write_feedback,
    write_report,
)

log = get_logger("omerion.agents.outcome_attribution")


@traced_node("load_deployment")
def load_node(state: AttributionState) -> AttributionState:
    dep = load_deployment(state.deployment_id)
    if not dep:
        raise RuntimeError(f"deployment {state.deployment_id} not found")
    state.go_live_at = dep["go_live_date"]
    state.client_id = dep.get("client_id")
    state.persona = persona_for(state.client_id)
    cfg = settings.agent("outcome_attribution")
    state.window_days = int(cfg.get("pre_post_window_days", 30))
    log.info("attribution_loaded", deployment_id=str(state.deployment_id),
             persona=state.persona, window_days=state.window_days)
    return state


@traced_node("compute_deltas")
def deltas_node(state: AttributionState) -> AttributionState:
    state.kpi_deltas = compute_deltas(
        persona=state.persona or "unknown",
        client_id=state.client_id,
        go_live_at=state.go_live_at,
        window_days=state.window_days,
    )
    pre_start, pre_end, post_start, post_end = _window(state.go_live_at, state.window_days)
    state.revenue_pre = sum_revenue(state.client_id, pre_start, pre_end)
    state.revenue_post = sum_revenue(state.client_id, post_start, post_end)
    state.conversion_rate_pre, _ = conversion_rate(state.client_id, pre_start, pre_end)
    state.conversion_rate_post, _ = conversion_rate(state.client_id, post_start, post_end)
    return state


@traced_node("summarize")
def summarize_node(state: AttributionState) -> AttributionState:
    router = ClaudeRouter()
    cfg = settings.agent("outcome_attribution")
    state.summary_md = render_summary(
        router,
        deployment_id=state.deployment_id,
        persona=state.persona or "unknown",
        window_days=state.window_days,
        threshold=float(cfg.get("min_delta_threshold", 0.10)),
        deltas=state.kpi_deltas,
        rev_pre=state.revenue_pre,
        rev_post=state.revenue_post,
        cr_pre=state.conversion_rate_pre,
        cr_post=state.conversion_rate_post,
    )
    state.proof_point = derive_proof_point(state.kpi_deltas)
    return state


@traced_node("feedback")
def feedback_node(state: AttributionState) -> AttributionState:
    router = ClaudeRouter()
    state.feedback = generate_feedback(
        router,
        deployment_id=state.deployment_id,
        persona=state.persona or "unknown",
        summary_md=state.summary_md,
        deltas=state.kpi_deltas,
    )
    return state


@traced_node("persist_report")
def persist_report_node(state: AttributionState) -> AttributionState:
    state.report_id = write_report(
        deployment_id=state.deployment_id,
        client_id=state.client_id,
        deltas=state.kpi_deltas,
        summary_md=state.summary_md,
        proof_point=state.proof_point,
        window_days=state.window_days,
    )
    return state


@traced_node("persist_feedback")
def persist_feedback_node(state: AttributionState) -> AttributionState:
    count = write_feedback(state.deployment_id, state.feedback)
    log.info("attribution_feedback_written", count=count)
    return state


@traced_node("embed_outcome")
def embed_outcome_node(state: AttributionState) -> AttributionState:
    """Write attribution summary to delivery_outcomes Pinecone namespace.

    Only fires when significant_count >= 1 — zero-delta reports are not
    useful proof-points for proposal synthesis. Failure is logged but
    never raised; emit_node must not be blocked by a Pinecone outage.
    """
    from datetime import date
    significant_count = sum(1 for d in state.kpi_deltas if d.significant)
    proof_point_kpi = ""
    if state.kpi_deltas:
        best = max(
            (d for d in state.kpi_deltas if d.significant),
            key=lambda d: abs(d.delta_pct),
            default=None,
        )
        proof_point_kpi = best.name if best else ""

    proposal = state.scratch.get("blueprint", {}).get("proposal", {}) if hasattr(state, "scratch") else {}
    service_package = proposal.get("recommended_service_package", "unknown")

    embed_outcome(
        report_id=state.report_id,
        deployment_id=state.deployment_id,
        client_id=state.client_id,
        persona=state.persona or "unknown",
        service_package=service_package,
        summary_md=state.summary_md or "",
        proof_point=state.proof_point or "",
        revenue_post=state.revenue_post or 0.0,
        kpi_count=len(state.kpi_deltas),
        significant_count=significant_count,
        delta_pct_max=max((abs(d.delta_pct) for d in state.kpi_deltas), default=0.0),
        proof_point_kpi=proof_point_kpi,
        run_date=date.today().isoformat(),
    )
    return state


@traced_node("emit")
def emit_node(state: AttributionState) -> AttributionState:
    emit_event(
        EventType.ATTRIBUTION_REPORT_READY,
        source_agent=state.agent_name,
        payload={
            "report_id": str(state.report_id) if state.report_id else None,
            "deployment_id": str(state.deployment_id),
            "client_id": str(state.client_id) if state.client_id else None,
            "proof_point": state.proof_point,
            "significant_count": sum(1 for d in state.kpi_deltas if d.significant),
            "revenue_delta_usd": round(state.revenue_post - state.revenue_pre, 2),
        },
        correlation_id=state.correlation_id,
    )
    # Filter to RD-backlog items only — icp_scoring_weights and offer_templates
    # are routed elsewhere via their own emit paths, and incorrectly bundling
    # them under RD_INSIGHTS_BATCH_READY causes R3 to attempt to synthesize
    # strategy proposals from non-RD feedback.
    rd_items = [f for f in state.feedback if f.target == "rd_backlog"]
    if rd_items:
        emit_event(
            EventType.RD_INSIGHTS_BATCH_READY,
            source_agent=state.agent_name,
            payload={
                "deployment_id": str(state.deployment_id),
                "items": [f.model_dump() for f in rd_items],
            },
            correlation_id=state.correlation_id,
        )
    return state


def build():
    g = StateGraph(AttributionState)
    g.add_node("load_deployment", load_node)
    g.add_node("compute_deltas", deltas_node)
    g.add_node("summarize", summarize_node)
    g.add_node("feedback", feedback_node)
    g.add_node("persist_report", persist_report_node)
    g.add_node("persist_feedback", persist_feedback_node)
    g.add_node("embed_outcome", embed_outcome_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("load_deployment")
    g.add_edge("load_deployment", "compute_deltas")
    g.add_edge("compute_deltas", "summarize")
    g.add_edge("summarize", "feedback")
    g.add_edge("feedback", "persist_report")
    g.add_edge("persist_report", "persist_feedback")
    g.add_edge("persist_feedback", "embed_outcome")
    g.add_edge("embed_outcome", "emit")
    g.add_edge("emit", END)
    return g.compile()
