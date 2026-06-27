"""LangGraph for STAGING CLEANUP (Agent #21).

Flow (cron — nightly):
    load_stale_environments   (fetch Railway services idle > 14 days)
      → perform_cleanup        (teardown each; mark cleaned in Supabase)
      → emit_summary           (log results; no external event emitted — internal housekeeping)

Design: purely deterministic housekeeping agent. No LLM, no HITL.
State is returned as a NEW dict per node (not mutated in place) so
LangGraph's reducer can merge partial updates correctly.
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .state import StagingCleanupState
from .tools import fetch_stale_environments, mark_environment_cleaned, teardown_railway_service

log = get_logger("omerion.agents.staging_cleanup")


@traced_node("load_stale_environments")
def load_stale_environments(state: StagingCleanupState) -> dict[str, Any]:
    envs = fetch_stale_environments(days_stale=14)
    log.info("staging_cleanup.loaded", count=len(envs))
    return {
        "stale_environments": envs,
        "cleaned_count": 0,
        "failed_count": 0,
    }


@traced_node("perform_cleanup")
def perform_cleanup(state: StagingCleanupState) -> dict[str, Any]:
    cleaned = 0
    failed = 0
    for env in state["stale_environments"]:
        service_id = env.get("service_id")
        env_id = env.get("id")
        if not service_id or not env_id:
            continue

        success = teardown_railway_service(service_id)
        if success:
            mark_environment_cleaned(env_id)
            cleaned += 1
        else:
            failed += 1
            log.warning("staging_cleanup.teardown_failed", service_id=service_id, env_id=env_id)

    return {"cleaned_count": cleaned, "failed_count": failed}


@traced_node("emit_summary")
def emit_summary(state: StagingCleanupState) -> dict[str, Any]:
    log.info(
        "staging_cleanup.completed",
        cleaned=state["cleaned_count"],
        failed=state["failed_count"],
        total=len(state.get("stale_environments", [])),
    )
    return {}


def build() -> StateGraph:
    graph = StateGraph(StagingCleanupState)
    graph.add_node("load_stale_environments", load_stale_environments)
    graph.add_node("perform_cleanup", perform_cleanup)
    graph.add_node("emit_summary", emit_summary)

    graph.set_entry_point("load_stale_environments")
    graph.add_edge("load_stale_environments", "perform_cleanup")
    graph.add_edge("perform_cleanup", "emit_summary")
    graph.add_edge("emit_summary", END)

    from omerion_core.runtime.checkpointer import get_checkpointer
    return graph.compile(checkpointer=get_checkpointer())
