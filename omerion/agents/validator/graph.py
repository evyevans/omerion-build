"""LangGraph for VALIDATOR.

Flow:
    fetch_context → analyze_diff → (lint gate) → verify_criteria → submit_review
                                              ↘ (lint fail fast path) ↗
"""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .prompts import VERIFY_SYSTEM, VERIFY_USER
from .state import LineComment, ValidatorState
from .tools import (
    _MAX_AUTO_REJECTIONS,
    _MAX_FILES_PER_REVIEW,
    chunk_diff_by_file,
    fetch_pr_diff,
    fetch_task_spec_for_branch,
    increment_pr_rejection_count,
    lint_diff,
    post_github_review,
    update_task_ci_status,
)

log = get_logger("omerion.agents.validator")


@traced_node("fetch_context")
def fetch_context_node(state: ValidatorState) -> ValidatorState:
    """Load TaskSpec from Supabase by matching PR head branch."""
    row = fetch_task_spec_for_branch(state.head_branch)
    if row is None:
        state.verdict = "reject"
        state.review_body = (
            f"**VALIDATOR**: No TaskSpec found in `build_tasks` for branch "
            f"`{state.head_branch}`. ORCHESTRATOR must link the task before "
            f"requesting a review."
        )
        return state

    state.task_id = UUID(row["task_id"])
    state.acceptance_criteria = row.get("acceptance_criteria") or []
    state.spec_md = row.get("spec_md") or ""
    return state


@traced_node("analyze_diff")
def analyze_diff_node(state: ValidatorState) -> ValidatorState:
    """Fetch PR diff and run deterministic lint checks."""
    if state.verdict == "reject":
        return state

    patch, changed_files = fetch_pr_diff(state.repo_full, state.pr_number)
    state.diff_patch = patch
    state.diff_files = changed_files

    if not patch:
        state.verdict = "reject"
        state.review_body = "**VALIDATOR**: PR diff is empty — no files changed."
        return state

    state.diff_chunks = chunk_diff_by_file(patch)
    if len(changed_files) > _MAX_FILES_PER_REVIEW:
        state.lint_errors.append(
            f"LINT: PR touches {len(changed_files)} files — maximum {_MAX_FILES_PER_REVIEW} per review. "
            "Split into smaller PRs."
        )

    state.lint_errors.extend(lint_diff(patch, changed_files))
    return state


def route_after_lint(state: ValidatorState) -> str:
    """Skip LLM if deterministic lint already found failures."""
    if state.verdict == "reject" or state.lint_errors:
        return "escalation_gate"
    return "verify_criteria"


@traced_node("verify_criteria")
def verify_criteria_node(state: ValidatorState) -> ValidatorState:
    """LLM check: diff vs acceptance_criteria. Only runs when lint is clean."""
    if state.verdict == "reject":
        return state

    criteria_block = "\n".join(f"- [ ] {c}" for c in state.acceptance_criteria)
    diff_to_review = (
        state.diff_patch
        if len(state.diff_patch) <= 12000
        else "\n\n".join(state.diff_chunks)
    )
    user_msg = VERIFY_USER.format(
        criteria_block=criteria_block,
        diff_patch=diff_to_review[:14000],
        spec_md=state.spec_md[:2000],
    )

    router = ClaudeRouter()
    resp = router.complete(
        tier=Tier.DEFAULT,                       # Sonnet (was the invalid string "sonnet")
        system=VERIFY_SYSTEM,
        prompt=user_msg,
        max_tokens=2048,
        temperature=0,
    )

    # router.complete() returns a dict {"text", ...} — parse the text, not the dict.
    # (Previously json.loads(resp) on the dict raised an *uncaught* TypeError, so the
    # validator crashed on every clean-lint PR and could never produce an "approve".)
    parsed, ok = extract_json_object(resp.get("text", ""))
    if not ok:
        state.verdict = "reject"
        state.review_body = "**VALIDATOR**: Internal error parsing LLM evaluation. Re-run required."
        log.error("validator_llm_json_parse_error", raw=str(resp.get("text", ""))[:200])
        return state

    state.verdict = parsed.get("verdict", "reject")
    state.review_body = parsed.get("review_body", "")
    state.line_comments = [LineComment(**c) for c in parsed.get("line_comments", [])]
    return state


@traced_node("escalation_gate")
def escalation_gate_node(state: ValidatorState) -> ValidatorState:
    """Consolidate the verdict and escalate to the founder after repeated rejections.

    Funnels both paths (lint-fail short-circuit and LLM verify). After
    _MAX_AUTO_REJECTIONS rejections of the same PR, raise an actionable founder
    decision (override-approve / abandon) instead of silently looping.
    """
    # Build a lint-based body if the LLM path was bypassed by the conditional edge.
    if state.lint_errors and state.verdict != "approve":
        state.verdict = "reject"
        if not state.review_body:
            state.review_body = "**VALIDATOR: REQUEST CHANGES**\n\n" + "\n".join(
                f"- {e}" for e in state.lint_errors
            )
    if state.verdict is None:
        state.verdict = "reject"

    if state.verdict == "reject" and state.task_id:
        count = increment_pr_rejection_count(state.task_id)
        if count >= _MAX_AUTO_REJECTIONS:
            review = create_founder_review_task(
                agent_name=state.agent_name,
                session_id=str(state.task_id),
                subject=f"VALIDATOR loop: PR #{state.pr_number} rejected {count}× — override or abandon?",
                context_md=(state.review_body or "")[:4000],
                draft_ref={
                    "pr_number": state.pr_number,
                    "repo_full": state.repo_full,
                    "task_id": str(state.task_id),
                },
                correlation_id=state.correlation_id,
            )
            state.escalation_review_id = review["review_id"]
            state.needs_decision = True
            log.warning("validator_hitl_escalation", pr=state.pr_number, count=count)
    return state


def route_after_escalation(state: ValidatorState) -> str:
    return "hitl_wait" if state.needs_decision else "submit_review"


@traced_node("hitl_wait")
def hitl_wait_node(state: ValidatorState) -> ValidatorState:
    """Pause for the founder's override/abandon decision on a stuck PR."""
    if not state.escalation_review_id:
        return state
    result = interrupt({"review_id": state.escalation_review_id, "session_id": str(state.task_id)})
    decisions = result.get("decisions", {}) if isinstance(result, dict) else {}
    decision = decisions.get(str(state.escalation_review_id), "rejected")

    if decision == "approved":
        # Founder override → post an APPROVED review so merge can proceed. The
        # orchestrator's G3 gate still applies at the actual merge-to-main.
        state.verdict = "approve"
        state.founder_overridden = True
        state.review_body = "**VALIDATOR — founder override: APPROVED.**\n\n" + (state.review_body or "")
    else:
        # Abandon → keep the rejection final.
        state.verdict = "reject"
        state.task_abandoned = True
    log.info("validator_escalation_resolved",
             overridden=state.founder_overridden, abandoned=state.task_abandoned)
    return state


@traced_node("submit_review")
def submit_review_node(state: ValidatorState) -> ValidatorState:
    """Post GitHub review, update Supabase, emit event."""
    post_github_review(
        repo_full=state.repo_full,
        pr_number=state.pr_number,
        verdict=state.verdict,
        review_body=state.review_body,
        line_comments=[c.model_dump() for c in state.line_comments],
    )

    if state.task_id:
        ci_status = "validator_approved" if state.verdict == "approve" else "validator_rejected"
        update_task_ci_status(state.task_id, ci_status)

    event_type = (
        EventType.PR_VALIDATION_APPROVED
        if state.verdict == "approve"
        else EventType.PR_VALIDATION_REJECTED
    )
    emit_event(
        event_type,
        source_agent=state.agent_name,
        payload={
            "pr_url": state.pr_url,
            "pr_number": state.pr_number,
            "repo_full": state.repo_full,
            "task_id": str(state.task_id) if state.task_id else None,
            "verdict": state.verdict,
        },
        correlation_id=state.correlation_id,
    )
    log.info("validator_run_complete", verdict=state.verdict, pr=state.pr_number)
    return state


def build():
    g = StateGraph(ValidatorState)
    g.add_node("fetch_context", fetch_context_node)
    g.add_node("analyze_diff", analyze_diff_node)
    g.add_node("verify_criteria", verify_criteria_node)
    g.add_node("escalation_gate", escalation_gate_node)
    g.add_node("hitl_wait", hitl_wait_node)
    g.add_node("submit_review", submit_review_node)

    g.set_entry_point("fetch_context")
    g.add_edge("fetch_context", "analyze_diff")
    g.add_conditional_edges(
        "analyze_diff",
        route_after_lint,
        {"verify_criteria": "verify_criteria", "escalation_gate": "escalation_gate"},
    )
    g.add_edge("verify_criteria", "escalation_gate")
    # After consolidation: escalate (interrupt for override/abandon) or post the review.
    g.add_conditional_edges(
        "escalation_gate",
        route_after_escalation,
        {"hitl_wait": "hitl_wait", "submit_review": "submit_review"},
    )
    g.add_edge("hitl_wait", "submit_review")
    g.add_edge("submit_review", END)

    # Checkpointer required: the escalation interrupt persists state across the
    # founder's override/abandon decision.
    from omerion_core.runtime.checkpointer import get_checkpointer
    return g.compile(checkpointer=get_checkpointer())
