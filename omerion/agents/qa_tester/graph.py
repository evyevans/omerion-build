"""LangGraph for QA_TESTER.

Flow:
    fetch_build_context
      → run_tests              (deterministic subprocess)
      → (route) → analyze_failures (LLM, only on failure)
                             ↘ skip to qa_gate (on pass)
      → qa_gate               (deterministic threshold check + HITL if fail)
      → emit                  (QA_TESTS_PASSED / QA_TESTS_FAILED)

LLM justification: analyze_failures is the ONLY LLM node. Invoked ONLY when
tests fail — interpreting multi-file pytest output against a spec cannot be
expressed as a deterministic predicate. All pass/fail gating is arithmetic.
"""
from __future__ import annotations

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node
from langgraph.graph import END, StateGraph

from .prompts import FAILURE_ANALYSIS_SYSTEM, FAILURE_ANALYSIS_USER
from .state import QATesterState
from .tools import (
    coverage_meets_threshold,
    fetch_build_task,
    fetch_build_task_by_slug,
    parse_coverage_pct,
    parse_pytest_output,
    persist_test_result,
    run_test_suite,
)

log = get_logger("omerion.agents.qa_tester")


@traced_node("fetch_build_context")
def fetch_build_context_node(state: QATesterState) -> QATesterState:
    """Node 1 — Load build task from Supabase. Deterministic.

    Resolution order:
    1. build_task_id (UUID) — direct PK lookup, used when event payload includes it.
    2. deployment_id + task_slug — composite lookup for BUILD_TASK_COMPLETED events
       where the builder omits task_id (current builder behaviour as of 2026-06-15).
    """
    row: dict | None = None

    if state.build_task_id:
        row = fetch_build_task(state.build_task_id)
    elif state.deployment_id and state.task_slug:
        row = fetch_build_task_by_slug(state.deployment_id, state.task_slug)
        if row:
            # Promote the resolved task_id into state for all downstream nodes.
            from uuid import UUID as _UUID
            resolved = row.get("task_id")
            if resolved:
                state.build_task_id = _UUID(str(resolved))

    if not row:
        log.warning(
            "qa_tester_build_task_not_found",
            build_task_id=str(state.build_task_id),
            deployment_id=str(state.deployment_id),
            task_slug=state.task_slug,
        )
        state.verdict = "failed"
        return state

    state.spec_md = row.get("spec_md") or ""
    state.acceptance_criteria = row.get("acceptance_criteria") or []
    if row.get("test_command"):
        state.test_command = row["test_command"]
    log.info("qa_tester_context_loaded", task_id=str(state.build_task_id))
    return state


@traced_node("run_tests")
def run_tests_node(state: QATesterState) -> QATesterState:
    """Node 2 — Execute test suite as subprocess. Fully deterministic."""
    if state.verdict == "failed":
        return state

    repo_root = getattr(settings, "repo_root", "/app")
    output = run_test_suite(state.test_command, repo_root=repo_root)
    raw = output["raw_output"]
    state.raw_output = raw

    counts = parse_pytest_output(raw)
    state.tests_total = counts["total"]
    state.tests_passed = counts["passed"]
    state.tests_failed = counts["failed"] + counts["errors"]
    state.coverage_pct = parse_coverage_pct(raw)

    log.info(
        "qa_tester_tests_run",
        total=state.tests_total,
        passed=state.tests_passed,
        failed=state.tests_failed,
        coverage=state.coverage_pct,
    )
    return state


def route_after_tests(state: QATesterState) -> str:
    """Route to LLM analysis only when tests fail or coverage is insufficient."""
    if state.verdict == "failed":
        return "qa_gate"
    if state.tests_failed > 0:
        return "analyze_failures"
    if not coverage_meets_threshold(state.coverage_pct, state.coverage_threshold):
        return "analyze_failures"
    return "qa_gate"


@traced_node("analyze_failures")
def analyze_failures_node(state: QATesterState) -> QATesterState:
    """Node 3 — LLM root-cause analysis. ONLY runs when tests fail.

    LLM justified: interpreting free-form pytest output against a spec and
    mapping failures to acceptance criteria requires language reasoning.
    The LLM does NOT decide pass/fail — that is deterministic arithmetic.
    """
    criteria_block = "\n".join(f"- {c}" for c in state.acceptance_criteria) or "(none)"
    router = ClaudeRouter()
    resp = router.complete(
        tier=Tier.DEFAULT,
        system=FAILURE_ANALYSIS_SYSTEM,
        prompt=FAILURE_ANALYSIS_USER.format(
            spec_md=state.spec_md[:2000],
            criteria_block=criteria_block,
            raw_output=state.raw_output[:6000],
        ),
        max_tokens=1024,
        temperature=0.0,
        agent_name=state.agent_name,
        run_id=str(state.run_id),
        correlation_id=str(state.correlation_id) if state.correlation_id else None,
    )
    state.record_llm(resp["usage"], resp["cost_usd"])

    parsed, ok = extract_json_object(resp.get("text", ""))
    if ok:
        state.failure_summary = (
            f"{parsed.get('root_cause', '')}\n\n"
            f"Suggested fix: {parsed.get('suggested_fix', '')}"
        )
    else:
        state.failure_summary = state.raw_output[-1000:]

    log.info("qa_tester_failure_analysis_complete")
    return state


@traced_node("qa_gate")
def qa_gate_node(state: QATesterState) -> QATesterState:
    """Node 4 — Deterministic pass/fail gate + HITL escalation on failure.

    Verdict is arithmetic: tests_failed == 0 AND coverage >= threshold.
    The LLM never influences this decision.
    """
    if state.verdict == "failed":
        return state

    tests_ok = state.tests_failed == 0
    coverage_ok = coverage_meets_threshold(state.coverage_pct, state.coverage_threshold)

    if tests_ok and coverage_ok:
        state.verdict = "passed"
        log.info("qa_tester_passed", task_id=str(state.build_task_id))
        return state

    state.verdict = "failed"
    failure_reason = []
    if not tests_ok:
        failure_reason.append(f"{state.tests_failed} test(s) failed")
    if not coverage_ok:
        failure_reason.append(
            f"coverage {state.coverage_pct:.0%} below threshold {state.coverage_threshold:.0%}"
        )

    context = (
        f"**QA_TESTER — Build Failed**\n\n"
        f"- Build task: `{state.build_task_id}`\n"
        f"- Failures: {', '.join(failure_reason)}\n\n"
        f"**Root cause analysis:**\n{state.failure_summary or '(no analysis)'}\n\n"
        f"**Raw output (last 2000 chars):**\n```\n{state.raw_output[-2000:]}\n```"
    )
    try:
        review = create_founder_review_task(
            agent_name=state.agent_name,
            session_id=state.session_id,
            subject=f"QA_TESTER: Build {state.build_task_id} failed — {', '.join(failure_reason)}",
            context_md=context,
            draft_ref={"build_task_id": str(state.build_task_id)},
            correlation_id=state.correlation_id,
        )
        state.hitl_review_id_str = review["review_id"]
    except Exception as exc:
        log.warning("qa_tester_hitl_create_failed", error=str(exc))

    log.warning("qa_tester_failed", reason=failure_reason, task_id=str(state.build_task_id))
    return state


@traced_node("emit")
def emit_node(state: QATesterState) -> QATesterState:
    """Node 5 — Persist result + emit terminal event."""
    persist_test_result(
        run_id=str(state.run_id),
        build_task_id=state.build_task_id,
        status=state.verdict or "error",
        tests_total=state.tests_total,
        tests_passed=state.tests_passed,
        tests_failed=state.tests_failed,
        coverage_pct=state.coverage_pct,
        failure_summary=state.failure_summary,
        raw_output=state.raw_output,
    )

    event_type = (
        EventType.QA_TESTS_PASSED if state.verdict == "passed"
        else EventType.QA_TESTS_FAILED
    )
    emit_event(
        event_type,
        source_agent=state.agent_name,
        payload={
            "build_task_id": str(state.build_task_id) if state.build_task_id else None,
            "tests_total": state.tests_total,
            "tests_passed": state.tests_passed,
            "tests_failed": state.tests_failed,
            "coverage_pct": state.coverage_pct,
            "verdict": state.verdict,
        },
        correlation_id=state.correlation_id,
    )
    log.info("qa_tester_emit_complete", verdict=state.verdict)
    return state


def build():
    """Compile QA_TESTER graph with checkpointer."""
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(QATesterState)
    g.add_node("fetch_build_context", fetch_build_context_node)
    g.add_node("run_tests", run_tests_node)
    g.add_node("analyze_failures", analyze_failures_node)
    g.add_node("qa_gate", qa_gate_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("fetch_build_context")
    g.add_edge("fetch_build_context", "run_tests")
    g.add_conditional_edges(
        "run_tests",
        route_after_tests,
        {"analyze_failures": "analyze_failures", "qa_gate": "qa_gate"},
    )
    g.add_edge("analyze_failures", "qa_gate")
    g.add_edge("qa_gate", "emit")
    g.add_edge("emit", END)

    return g.compile(checkpointer=get_checkpointer())
