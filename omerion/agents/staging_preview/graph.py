"""LangGraph for STAGING PREVIEW (Agent #20).

Flow:
    load_deployment      (validate all tasks merged)
      → provision_staging (spin up Railway preview service)
      → run_tests         (synthetic health + smoke tests)
      → [present_preview] (G2 HITL — client approves preview URL)
      → report_results    (persist + emit staging.validated | staging.failed)
      → teardown_staging  (destroy Railway service)

G2 correctness: the HITL interrupt happens AFTER synthetic tests pass — the
client only sees the preview URL when Omerion already considers it green.
Failure at any stage short-circuits to report_results (still tears down).
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .state import StagingState
from .tools import (
    persist_staging_results,
    provision_railway_service,
    run_synthetic_tests,
    teardown_railway_service,
    validate_deployment_tasks_merged,
)

log = get_logger("omerion.agents.staging_preview")


@traced_node("load_deployment")
def load_deployment(state: StagingState) -> dict[str, Any]:
    deployment_id = state.get("deployment_id")
    if not deployment_id:
        log.error("staging_missing_deployment_id")
        return {"all_tests_passed": False}

    if not validate_deployment_tasks_merged(deployment_id):
        log.error("staging_tasks_not_merged", deployment_id=deployment_id)
        return {"all_tests_passed": False}

    return {}


@traced_node("provision_staging")
def provision_staging(state: StagingState) -> dict[str, Any]:
    if state.get("all_tests_passed") is False:
        return {}

    deployment_id = state.get("deployment_id")
    info = provision_railway_service(deployment_id)
    return {
        "staging_service_id": info["service_id"],
        "preview_url": info["preview_url"],
    }


@traced_node("run_tests")
def run_tests(state: StagingState) -> dict[str, Any]:
    if state.get("all_tests_passed") is False:
        return {}

    preview_url = state.get("preview_url")
    if not preview_url:
        return {"all_tests_passed": False}

    results = run_synthetic_tests(preview_url)
    all_passed = all(t["passed"] for t in results)
    return {
        "test_results": results,
        "all_tests_passed": all_passed,
    }


@traced_node("present_preview")
def present_preview(state: StagingState) -> dict[str, Any]:
    """G2 HITL gate — client approves the live preview URL."""
    action = interrupt({
        "type": "client_preview_validation",
        "preview_url": state.get("preview_url"),
        "test_results": state.get("test_results"),
    })
    decision = action.get("decision", "rejected") if isinstance(action, dict) else "rejected"
    return {"client_decision": decision}


def route_after_tests(state: StagingState) -> str:
    return "present_preview" if state.get("all_tests_passed") else "report_results"


@traced_node("report_results")
def report_results(state: StagingState) -> dict[str, Any]:
    dep_id = state.get("deployment_id")
    srv_id = state.get("staging_service_id")
    url = state.get("preview_url")
    tests = state.get("test_results") or []

    if srv_id and url:
        persist_staging_results(dep_id, srv_id, url, tests)

    decision = state.get("client_decision")
    passed = state.get("all_tests_passed")

    if passed and decision == "approved":
        emit_event(
            EventType.STAGING_VALIDATED,
            source_agent="staging_preview",
            payload={"deployment_id": dep_id, "preview_url": url},
        )
        log.info("staging_validated", deployment_id=dep_id)
    else:
        emit_event(
            EventType.STAGING_FAILED,
            source_agent="staging_preview",
            payload={
                "deployment_id": dep_id,
                "all_tests_passed": bool(passed),
                "client_decision": decision,
            },
        )
        log.warning("staging_failed", deployment_id=dep_id, passed=passed, decision=decision)

    return {}


@traced_node("teardown_staging")
def teardown_staging(state: StagingState) -> dict[str, Any]:
    srv_id = state.get("staging_service_id")
    if srv_id:
        teardown_railway_service(srv_id)
        log.info("staging_teardown_complete", service_id=srv_id)
    return {"teardown_scheduled": True}


def build() -> StateGraph:
    workflow = StateGraph(StagingState)

    workflow.add_node("load_deployment", load_deployment)
    workflow.add_node("provision_staging", provision_staging)
    workflow.add_node("run_tests", run_tests)
    workflow.add_node("present_preview", present_preview)
    workflow.add_node("report_results", report_results)
    workflow.add_node("teardown_staging", teardown_staging)

    workflow.set_entry_point("load_deployment")
    workflow.add_edge("load_deployment", "provision_staging")
    workflow.add_edge("provision_staging", "run_tests")
    workflow.add_conditional_edges("run_tests", route_after_tests, {
        "present_preview": "present_preview",
        "report_results": "report_results",
    })
    workflow.add_edge("present_preview", "report_results")
    workflow.add_edge("report_results", "teardown_staging")
    workflow.add_edge("teardown_staging", END)

    from omerion_core.runtime.checkpointer import get_checkpointer
    return workflow.compile(checkpointer=get_checkpointer())
