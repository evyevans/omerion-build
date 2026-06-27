"""LangGraph for R1 Market/Tech Watcher.

Flow:
    fetch → filter → tag → dedup → persist → index → emit

`dedup` is dual-threshold semantic dedup on the tagged summaries (≥0.96 hard-skip,
0.90–0.95 soft-flag), complementing the URL dedup inside `persist`.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .state import WatcherState
from .tools import (
    fetch_signals,
    index_insights,
    is_relevant,
    persist_insights,
    semantic_dedup,
    tag_signal,
)

log = get_logger("omerion.agents.r1_market_tech_watcher")


@traced_node("fetch")
def fetch_node(state: WatcherState) -> WatcherState:
    state.raw = fetch_signals()
    log.info("r1_fetched", count=len(state.raw))
    return state


@traced_node("filter")
def filter_node(state: WatcherState) -> WatcherState:
    state.raw = [s for s in state.raw if is_relevant(s)]
    return state


@traced_node("tag")
def tag_node(state: WatcherState) -> WatcherState:
    if not state.raw:
        return state
    router = ClaudeRouter()
    for s in state.raw:
        ins = tag_signal(router, s)
        if ins is not None:
            state.insights.append(ins)
    return state


@traced_node("dedup")
def dedup_node(state: WatcherState) -> WatcherState:
    """Dual-threshold semantic dedup on tagged summaries (the day-one design).

    Catches the same story under different URLs (which URL dedup misses).
    ≥0.96 → dropped here; 0.90–0.95 → kept with near_duplicate metadata for R3.
    """
    if not state.insights:
        return state
    kept, hard = semantic_dedup(state.insights)
    state.insights = kept
    state.semantic_duplicates = hard
    log.info("r1_dedup_complete", kept=len(kept), semantic_duplicates=hard)
    return state


@traced_node("persist")
def persist_node(state: WatcherState) -> WatcherState:
    written, dupes = persist_insights(state.insights)
    state.inserted = written
    state.duplicates = dupes
    return state


@traced_node("index")
def index_node(state: WatcherState) -> WatcherState:
    index_insights([i for i in state.insights if i.insight_id is not None])
    return state


@traced_node("emit")
def emit_node(state: WatcherState) -> WatcherState:
    """Emit one RD_INSIGHT_CREATED per persisted insight.

    Per-item events let R3 subscribe and process insights individually
    instead of waking on every batch and re-filtering. Insights without
    insight_id (failed to persist) are skipped.
    """
    if not state.inserted:
        return state
    for i in state.insights:
        if i.insight_id is None:
            continue
        emit_event(
            EventType.RD_INSIGHT_CREATED,
            source_agent=state.agent_name,
            payload={
                "run_date": state.run_date.isoformat(),
                "insight_id": str(i.insight_id),
                "title": i.title,
                "impact_tag": i.impact_tag,
                "estimated_priority": i.estimated_priority,
                "source_url": i.source_url,
                "source_type": i.source_type,
            },
            correlation_id=state.correlation_id,
        )
    try:
        from omerion_core.runtime.agent_coordinator import mark_agent_complete
        mark_agent_complete("r1-market-tech-watcher")
    except Exception as exc:  # noqa: BLE001
        log.warning("r1_coordinator_mark_failed", error=str(exc))
    return state


def build():
    g = StateGraph(WatcherState)
    g.add_node("fetch", fetch_node)
    g.add_node("filter", filter_node)
    g.add_node("tag", tag_node)
    g.add_node("dedup", dedup_node)
    g.add_node("persist", persist_node)
    g.add_node("index", index_node)
    g.add_node("emit", emit_node)
    g.set_entry_point("fetch")
    g.add_edge("fetch", "filter")
    g.add_edge("filter", "tag")
    g.add_edge("tag", "dedup")
    g.add_edge("dedup", "persist")
    g.add_edge("persist", "index")
    g.add_edge("index", "emit")
    g.add_edge("emit", END)
    return g.compile()
