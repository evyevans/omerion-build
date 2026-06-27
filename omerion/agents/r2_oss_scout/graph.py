"""LangGraph for R2 OSS Scout.

Flow:
    discover → filter → analyze → persist → emit
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .state import OssScoutState
from .tools import (
    analyze_repo,
    discover_candidates,
    passes_floor,
    persist_candidates,
    seed_terms_from_insight,
)

log = get_logger("omerion.agents.r2_oss_scout")


@traced_node("discover")
def discover_node(state: OssScoutState) -> OssScoutState:
    # Event-triggered runs carry the R1 insight (mapped into state by event_ingress);
    # focus discovery on it. Cron runs have no insight → static-tag discovery.
    state.seed_terms = seed_terms_from_insight(state.insight_title, state.insight_impact_tag)
    state.raw = discover_candidates(seed_terms=state.seed_terms or None)
    log.info("r2_discovered", count=len(state.raw), seeded=bool(state.seed_terms))
    return state


@traced_node("filter")
def filter_node(state: OssScoutState) -> OssScoutState:
    state.raw = [r for r in state.raw if passes_floor(r)]
    return state


@traced_node("analyze")
def analyze_node(state: OssScoutState) -> OssScoutState:
    if not state.raw:
        return state
    router = ClaudeRouter()
    for repo in state.raw:
        sc = analyze_repo(router, repo)
        if sc is not None:
            state.scored.append(sc)
    return state


@traced_node("persist")
def persist_node(state: OssScoutState) -> OssScoutState:
    written, dupes = persist_candidates(state.scored)
    state.inserted = written
    state.duplicates = dupes
    return state


@traced_node("emit")
def emit_node(state: OssScoutState) -> OssScoutState:
    """Emit one OSS_CANDIDATE_SCORED per persisted candidate.

    Per-item events let R3 subscribe and consider each candidate on its own
    merits. Candidates without candidate_id (failed to persist) are skipped.
    A single ANALYSIS_READY heartbeat is kept for batch-level observability.
    """
    if not state.inserted:
        return state
    for s in state.scored:
        if s.candidate_id is None:
            continue
        emit_event(
            EventType.OSS_CANDIDATE_SCORED,
            source_agent=state.agent_name,
            payload={
                "run_date": state.run_date.isoformat(),
                "candidate_id": str(s.candidate_id),
                "repo_url": s.repo.repo_url,
                "name": s.repo.name,
                "impact_tag": s.impact_tag,
                "integration_type": s.integration_type,
                "fit": s.rubric.fit,
                "risk": s.rubric.risk,
                "overall": s.rubric.overall,
            },
            correlation_id=state.correlation_id,
        )
    emit_event(
        EventType.ANALYSIS_READY,
        source_agent=state.agent_name,
        payload={
            "run_date": state.run_date.isoformat(),
            "kind": "oss_candidates",
            "inserted": state.inserted,
            "top_overall": max((s.rubric.overall for s in state.scored), default=0.0),
        },
        correlation_id=state.correlation_id,
    )
    try:
        from omerion_core.runtime.agent_coordinator import mark_agent_complete
        mark_agent_complete("r2-oss-scout")
    except Exception as exc:  # noqa: BLE001
        log.warning("r2_coordinator_mark_failed", error=str(exc))
    return state


def build():
    g = StateGraph(OssScoutState)
    g.add_node("discover", discover_node)
    g.add_node("filter", filter_node)
    g.add_node("analyze", analyze_node)
    g.add_node("persist", persist_node)
    g.add_node("emit", emit_node)
    g.set_entry_point("discover")
    g.add_edge("discover", "filter")
    g.add_edge("filter", "analyze")
    g.add_edge("analyze", "persist")
    g.add_edge("persist", "emit")
    g.add_edge("emit", END)
    return g.compile()
