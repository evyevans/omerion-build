"""LangGraph for BUILDER (Agent #11).

Flow:
    load_tasks
      → dispatch_builds           (conditional edge: Send one branch per pending/failed task)
      → build_single_task         (per-task: write→test→commit→PR, up to max_retries)
      → collect_results           (fan-in: merge results; conditional: failures → escalate)
      → [hitl_escalate]           (only if failed tasks remain and retry budget remains)
      → [hitl_wait]
      → emit_summary
"""
from __future__ import annotations

import time
from uuid import UUID

from langgraph.graph import END, StateGraph
from langgraph.types import Send, interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import claude
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .state import BuilderState, TaskResult
from .tools import (
    author_pr_body,
    check_banned_paths,
    check_secret_patterns,
    commit_changes,
    generate_code_changes,
    get_file_contents,
    get_file_tree,
    load_full_tasks,
    open_pr,
    pr_already_exists,
    run_tests_in_sandbox,
    update_task_status,
)

log = get_logger("omerion.agents.builder")

_RETRY_DELAYS_S = (0, 15, 45)


@traced_node("load_tasks")
def load_tasks_node(state: BuilderState) -> BuilderState:
    payload = state.scratch.get("event_payload", {})
    deployment_id = UUID(payload["deployment_id"])
    state.deployment_id = deployment_id
    state.blueprint_id = UUID(payload["blueprint_id"])
    state.repo_full_name = settings.github_build_repo or ""

    slugs = [t["slug"] for t in payload.get("tasks", [])]
    raw = load_full_tasks(deployment_id, slugs)

    state.tasks = [
        TaskResult(
            slug=r["slug"],
            task_id=UUID(r["task_id"]),
            branch_name=r.get("branch_name") or "",
            title=r.get("title") or "",
            acceptance_criteria=r.get("acceptance_criteria") or [],
            rationale=r.get("rationale") or "",
            spec_md=r.get("spec_md") or "",
        )
        for r in raw
        if r.get("branch_name")
    ]

    from omerion_core.clients.supabase_client import supabase
    bp_resp = supabase.table("automation_blueprints").select("workflow_spec_md").eq(
        "blueprint_id", str(state.blueprint_id)
    ).execute()
    workflow_spec_md = ""
    if bp_resp.data and bp_resp.data[0].get("workflow_spec_md"):
        workflow_spec_md = bp_resp.data[0]["workflow_spec_md"]
    state.scratch["workflow_spec_md"] = workflow_spec_md

    log.info("builder.tasks_loaded", count=len(state.tasks))
    return state


# ─── Dispatch (fan-out entry point) ──────────────────────────────────────────

@traced_node("dispatch_builds")
def dispatch_builds_node(state: BuilderState) -> BuilderState:
    """No-op node — exists so the conditional edge function has a named node to attach to."""
    return state


def _dispatch_sends(state: BuilderState) -> list[Send] | str:
    """Conditional edge: Send one branch per pending/failed task.

    On retry, already-succeeded tasks (status='pr_open') are skipped — the
    idempotency guard inside build_single_task also protects against duplicate
    PRs, but filtering here avoids wasted LLM calls.
    """
    to_dispatch = [t for t in state.tasks if t.status in ("pending", "failed")]
    if not to_dispatch:
        return "emit_summary"
    return [Send("build_single_task", {"current_task_result": t}) for t in to_dispatch]


# ─── Per-task build (one LangGraph branch per Send) ──────────────────────────

@traced_node("build_single_task")
def build_single_task_node(state: BuilderState) -> dict:
    """Build one task. Receives the TaskResult via Send payload in current_task_result."""
    task = state.current_task_result
    if task is None:
        log.error("builder.no_current_task")
        return {"completed_tasks": []}

    cfg = settings.agent("builder")
    max_retries: int = cfg["max_retries"]
    test_command: str = cfg["test_command"]
    test_timeout: int = cfg["test_timeout_seconds"]
    banned_paths: list[str] = cfg["guardrails"]["banned_path_prefixes"]
    secret_patterns: list[str] = cfg["guardrails"]["secret_patterns"]
    base_branch: str = cfg["pr_base_branch"]

    router = claude()

    # Idempotency guard — skip if PR already exists for this branch
    exists, pr_num, pr_url = pr_already_exists(task.branch_name)
    if exists:
        task.pr_number = pr_num
        task.pr_url = pr_url
        task.status = "pr_open"
        log.info("builder.pr_exists_skip", slug=task.slug, pr_url=pr_url)
        return {"completed_tasks": [task]}

    file_tree = get_file_tree(task.branch_name)
    relevant_paths = [p for p in file_tree if any(
        kw in p for kw in [task.slug.replace("-", "_"), "test", "conftest"]
    )][:8] or file_tree[:5]
    file_contents = get_file_contents(relevant_paths, task.branch_name)
    workflow_spec_md = state.scratch.get("workflow_spec_md", "")

    error_context = ""
    success = False

    for attempt in range(1, max_retries + 1):
        task.attempts = attempt
        if attempt > 1:
            delay = _RETRY_DELAYS_S[min(attempt - 1, len(_RETRY_DELAYS_S) - 1)]
            log.info("builder.retry_backoff", slug=task.slug, attempt=attempt, delay_s=delay)
            time.sleep(delay)
        log.info("builder.attempt", slug=task.slug, attempt=attempt)

        changes, resp = generate_code_changes(
            router, task, workflow_spec_md, file_tree, file_contents, error_context
        )
        state.record_llm(resp.get("usage", {}), resp.get("cost_usd", 0.0))

        if not changes:
            error_context = "Claude returned no file changes. Ensure you output a valid JSON array."
            continue

        if check_banned_paths(changes, banned_paths):
            error_context = (
                f"REJECTED: changes target a banned path (prefixes: {banned_paths}). "
                "Only modify application code."
            )
            continue
        if check_secret_patterns(changes, secret_patterns):
            error_context = (
                "REJECTED: changes contain a secret pattern. "
                "Use os.environ[] references only."
            )
            continue

        passed, test_output = run_tests_in_sandbox(
            changes, task.branch_name, test_command, test_timeout,
            isolation_key=f"{state.deployment_id}-{task.slug}",
        )
        task.last_test_output = test_output
        log.info("builder.test_result", slug=task.slug, attempt=attempt, passed=passed)

        if passed:
            commit_msg = f"feat: {task.title} [BUILDER attempt {attempt}]"
            task.commit_sha = commit_changes(changes, task.branch_name, commit_msg)

            pr_body = author_pr_body(router, task, changes, test_command)
            pr_num, pr_url = open_pr(task, pr_body, base_branch)
            task.pr_number = pr_num
            task.pr_url = pr_url
            task.status = "pr_open"

            update_task_status(task.task_id, "pr_open", pr_url=pr_url, pr_number=pr_num)
            log.info("builder.pr_opened", slug=task.slug, pr_url=pr_url)
            success = True
            break
        else:
            error_context = test_output

    if not success:
        task.status = "failed"
        task.notes = (
            f"Exhausted {max_retries} attempts. "
            f"Last test output:\n{task.last_test_output[:800]}"
        )
        update_task_status(task.task_id, "failed", notes=task.notes)
        log.error("builder.task_failed", slug=task.slug, attempts=max_retries)

    return {"completed_tasks": [task]}


# ─── Fan-in ───────────────────────────────────────────────────────────────────

@traced_node("collect_results")
def collect_results_node(state: BuilderState) -> dict:
    """Fan-in: merge accumulated completed_tasks back into tasks (authoritative list)."""
    return {"tasks": state.completed_tasks}


def _max_founder_retries() -> int:
    return int(settings.agent("builder").get("max_founder_retries", 1))


def _has_failures(state: BuilderState) -> str:
    has_failed = any(t.status == "failed" for t in state.tasks)
    if has_failed and state.founder_retry_count < _max_founder_retries():
        return "hitl_escalate"
    return "emit_summary"


# ─── HITL escalation (unchanged from original) ───────────────────────────────

@traced_node("hitl_escalate")
def hitl_escalate_node(state: BuilderState) -> BuilderState:
    failed = [t for t in state.tasks if t.status == "failed"]
    max_retries = settings.agent("builder")["max_retries"]
    context_md = (
        f"**{len(failed)} task(s) failed after {max_retries} automatic attempts each.**\n\n"
        "**Approve** to retry the failed tasks once more · **Reject** to abandon them "
        "(they'll emit `BUILD_TASK_FAILED`).\n\n"
        + "\n".join(
            f"### {t.slug}\n**Branch:** `{t.branch_name}`\n\n"
            f"**Last test output:**\n```\n{t.last_test_output[:600]}\n```"
            for t in failed
        )
    )
    review = create_founder_review_task(
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        subject=f"BUILDER failure — retry or abandon? deployment {state.deployment_id} ({len(failed)} task(s))",
        context_md=context_md,
        draft_ref={
            "deployment_id": str(state.deployment_id),
            "failed_slugs": [t.slug for t in failed],
        },
        correlation_id=state.correlation_id,
    )
    state.builder_hitl_review_id = review["review_id"]
    state.hitl_review_id = review["review_id"]
    return state


@traced_node("hitl_wait")
def hitl_wait_node(state: BuilderState) -> BuilderState:
    if not state.builder_hitl_review_id:
        state.retry_requested = False
        return state
    result = interrupt({"review_id": str(state.builder_hitl_review_id), "session_id": state.session_id})
    decisions = result.get("decisions", {}) if isinstance(result, dict) else {}
    decision = decisions.get(str(state.builder_hitl_review_id), "rejected")
    state.retry_requested = decision == "approved"
    if state.retry_requested:
        state.founder_retry_count += 1
        state.builder_hitl_review_id = None
    log.info("builder.escalation_decision", retry=state.retry_requested,
             founder_retry_count=state.founder_retry_count)
    return state


def _after_escalation(state: BuilderState) -> str:
    return "dispatch_builds" if state.retry_requested else "emit_summary"


@traced_node("emit_summary")
def emit_summary_node(state: BuilderState) -> BuilderState:
    for task in state.tasks:
        if task.status == "pr_open" and task.pr_url:
            emit_event(
                EventType.BUILD_TASK_COMPLETED,
                source_agent=state.agent_name,
                payload={
                    "deployment_id": str(state.deployment_id),
                    "task_slug": task.slug,
                    "pr_url": task.pr_url,
                    "pr_number": task.pr_number,
                    "attempts": task.attempts,
                },
                correlation_id=state.correlation_id,
            )
        else:
            state.failed_slugs.append(task.slug)
            emit_event(
                EventType.BUILD_TASK_FAILED,
                source_agent=state.agent_name,
                payload={
                    "deployment_id": str(state.deployment_id),
                    "task_slug": task.slug,
                    "failure_reason": task.notes,
                    "attempts": task.attempts,
                },
                correlation_id=state.correlation_id,
            )
            update_task_status(task.task_id, "failed", notes=task.notes)

    log.info(
        "builder.summary",
        completed=sum(1 for t in state.tasks if t.status == "pr_open"),
        failed=len(state.failed_slugs),
    )
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer

    g = StateGraph(BuilderState)
    g.add_node("load_tasks", load_tasks_node)
    g.add_node("dispatch_builds", dispatch_builds_node)
    g.add_node("build_single_task", build_single_task_node)
    g.add_node("collect_results", collect_results_node)
    g.add_node("hitl_escalate", hitl_escalate_node)
    g.add_node("hitl_wait", hitl_wait_node)
    g.add_node("emit_summary", emit_summary_node)

    g.set_entry_point("load_tasks")
    g.add_edge("load_tasks", "dispatch_builds")
    # Fan-out: each task → its own build_single_task branch
    g.add_conditional_edges("dispatch_builds", _dispatch_sends, ["build_single_task", "emit_summary"])
    # Fan-in: all branches converge at collect_results
    g.add_edge("build_single_task", "collect_results")
    g.add_conditional_edges("collect_results", _has_failures, {
        "hitl_escalate": "hitl_escalate",
        "emit_summary": "emit_summary",
    })
    g.add_edge("hitl_escalate", "hitl_wait")
    g.add_conditional_edges("hitl_wait", _after_escalation, {
        "dispatch_builds": "dispatch_builds",   # retry → re-dispatch only failed tasks
        "emit_summary": "emit_summary",
    })
    g.add_edge("emit_summary", END)

    return g.compile(checkpointer=get_checkpointer())
