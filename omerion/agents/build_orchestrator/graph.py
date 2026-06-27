"""LangGraph for Build Orchestrator (Agent #9).

Flow:
    load_blueprint
      → decompose (Claude Opus → TaskSpec[])
      → create_deployment_row
      → persist_tasks
      → build_tasks           (fan-out: issue → branch → inject → poll_pr → CI; STOPS at ci_pass)
      → hitl_gate             (G3 — founder approves BEFORE anything merges to main)
      → merge_tasks           (merge approved PRs → main = deploy trigger)
      → finalize_deployment   (mark live or failed)
      → emit                  (deployment.live | deployment.failed)

G3 correctness: the merge-to-main IS the deploy trigger (external CI/CD picks up
from main), so the founder gate must sit BEFORE merge_tasks — not after. The
automated validator-approval check inside merge_pr() is an additional guard.
"""
from __future__ import annotations

import json

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.policy import Gate, ReviewItem, gate
from omerion_core.llm.router import claude
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .state import BuildState, TaskSpec
from .tools import (
    _POLL_TIMEOUT_S,
    author_client_doc,
    create_branch,
    create_client_doc,
    create_issue,
    ensure_client_drive_folder,
    inject_to_cursor,
    load_blueprint,
    fetch_deployment_tasks,
    merge_pr,
    poll_pr,
    update_deployment,
    update_task,
    upsert_client_record,
)

log = get_logger("omerion.agents.build_orchestrator")


@traced_node("load_deployment_tasks")
def load_deployment_tasks_node(state: BuildState) -> BuildState:
    if state.mode == "client" and state.client_id is None:
        raise ValueError(
            "build_orchestrator: client mode requires client_id"
        )
    
    if not state.deployment_id:
        raise ValueError("build_orchestrator: requires deployment_id from build.task.created event")
        
    blueprint = load_blueprint(state.blueprint_id)
    state.scratch["blueprint"] = blueprint
    state.scratch["blueprint_summary"] = json.dumps({
        "persona": blueprint.get("persona"),
        "w5h": blueprint.get("w5h"),
        "ttwa": blueprint.get("ttwa"),
        "proposal": blueprint.get("proposal"),
    }, default=str)
    
    if not state.repo_full_name:
        state.repo_full_name = settings.github_build_repo or ""
        
    state.tasks = fetch_deployment_tasks(state.deployment_id)
    # Topo-sort here so dispatch can fan-out in dependency order
    state.tasks = _topological_sort(state.tasks)
    return state


def _topological_sort(tasks: list[TaskSpec]) -> list[TaskSpec]:
    """Kahn's algorithm — reorder tasks so every task runs after its depends_on."""
    by_slug = {t.slug: t for t in tasks}
    in_degree: dict[str, int] = {t.slug: 0 for t in tasks}
    dependents: dict[str, list[str]] = {t.slug: [] for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            if dep in by_slug:
                in_degree[t.slug] += 1
                dependents[dep].append(t.slug)
    queue = [s for s, d in in_degree.items() if d == 0]
    ordered: list[TaskSpec] = []
    while queue:
        slug = queue.pop(0)
        ordered.append(by_slug[slug])
        for child in dependents[slug]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
    # Append any tasks not reached (cycle or unknown dep slug) in original order
    seen = {t.slug for t in ordered}
    for t in tasks:
        if t.slug not in seen:
            ordered.append(t)
    return ordered


# ─── Fan-out ─────────────────────────────────────────────────────────────────

@traced_node("dispatch_task_builds")
def dispatch_task_builds_node(state: BuildState) -> BuildState:
    """No-op node — exists to anchor the fan-out conditional edge."""
    return state


def _dispatch_task_sends(state: BuildState) -> list[Send] | str:
    """Fan-out: one Send per task. Already-merged tasks are skipped."""
    pending = [t for t in state.tasks if t.status not in ("merged", "ci_pass")]
    if not pending:
        return "hitl_gate"
    return [
        Send("build_single_task", {
            "current_task": t,
            "scratch": state.scratch,
            "repo_full_name": state.repo_full_name,
            "client_slug": state.client_slug,
        })
        for t in pending
    ]


@traced_node("build_single_task")
def build_single_task_node(state: BuildState) -> dict:
    """Build one task (issue → branch → Cursor inject → poll PR → CI)."""
    task = state.current_task
    if task is None:
        log.error("build_orchestrator.no_current_task")
        return {"built_tasks": []}

    blueprint_summary = state.scratch.get("blueprint_summary", "")
    try:
        task.issue_number = create_issue(task, blueprint_summary)
        task.status = "issue_created"
        update_task(task)

        task.branch_name = create_branch(task, state.client_slug)
        task.status = "branch_open"
        update_task(task)

        inject_to_cursor(task, task.branch_name, blueprint_summary)

        pr = poll_pr(task.branch_name, timeout_seconds=_POLL_TIMEOUT_S)
        if pr is None or pr.get("timeout_reason") == "no_pr_opened":
            task.status = "failed"
            task.notes = "no PR observed before timeout (Cursor did not produce a PR)"
            update_task(task)
            return {"built_tasks": [task]}
        if pr.get("timeout_reason") == "ci_pending":
            task.pr_number = pr["number"]
            task.pr_url = pr["url"]
            task.ci_status = "pending"
            task.status = "failed"
            task.notes = "PR opened but CI did not finish before timeout"
            update_task(task)
            return {"built_tasks": [task]}

        task.pr_number = pr["number"]
        task.pr_url = pr["url"]
        task.ci_status = pr["ci_status"]
        task.status = "pr_open"
        update_task(task)

        if pr["ci_status"] != "success":
            task.status = "ci_fail"
            update_task(task)
            return {"built_tasks": [task]}

        task.status = "ci_pass"
        update_task(task)

    except Exception as exc:
        log.error(
            "build_task_failed",
            task_slug=task.slug,
            error=str(exc),
            error_class=type(exc).__name__,
            exc_info=True,
        )
        task.status = "failed"
        task.notes = f"{type(exc).__name__}: {exc}"
        update_task(task)

    return {"built_tasks": [task]}


@traced_node("collect_task_results")
def collect_task_results_node(state: BuildState) -> dict:
    """Fan-in: merge built_tasks (per-branch results) into authoritative tasks list."""
    return {"tasks": state.built_tasks}


@traced_node("hitl_gate")
def hitl_gate_node(state: BuildState) -> BuildState:
    """G3 — deploy/infra gate. Founder approves BEFORE any PR merges to main.

    Routed through the global HITL policy. Fail-closed: no approval → nothing
    merges (merge_tasks becomes a no-op), deployment is marked failed.
    """
    ready = [t for t in state.tasks if t.status == "ci_pass"]
    failed = [t for t in state.tasks if t.status in ("ci_fail", "failed")]

    if not ready:
        # Nothing passed CI — no deploy to approve. Skip the gate.
        state.deployment_approved = False
        log.info("build_no_mergeable_tasks", failed=len(failed))
        return state

    context_md = (
        f"**Ready to merge & deploy:** {len(ready)}  |  **Failed:** {len(failed)}\n\n"
        "⚠️ Approving merges these PRs to `main`, which triggers the live deploy.\n\n"
        + "\n".join(f"- ✅ #{t.pr_number} — {t.title} ({t.pr_url})" for t in ready)
        + ("\n" + "\n".join(f"- ❌ {t.title} ({t.notes or t.status})" for t in failed) if failed else "")
    )
    item = ReviewItem(
        key=state.session_id or "deploy",
        subject=f"Deploy approval — blueprint {state.blueprint_id}",
        context_md=context_md,
        draft_ref={
            "deployment_id": str(state.deployment_id),
            "blueprint_id": str(state.blueprint_id),
            "prs": [t.pr_url for t in ready if t.pr_url],
        },
    )
    decisions = gate(
        Gate.DEPLOY_OR_INFRA,
        [item],
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        correlation_id=state.correlation_id,
    )
    state.deployment_approved = decisions.get(item.key) == "approved"
    log.info("build_deploy_decision", approved=state.deployment_approved, ready=len(ready))
    return state


@traced_node("merge_tasks")
def merge_tasks_node(state: BuildState) -> BuildState:
    """Merge approved PRs to main (= deploy). No-op unless the G3 gate approved.

    merge_pr() additionally refuses to merge any PR lacking a VALIDATOR approval —
    an automated second guard layered under the founder's G3 sign-off.
    """
    if not state.deployment_approved:
        log.info("build_deploy_rejected_no_merge", count=len([t for t in state.tasks if t.status == "ci_pass"]))
        return state
    for task in state.tasks:
        if task.status != "ci_pass":
            continue
        if merge_pr(task.pr_number):
            task.status = "merged"
            update_task(task)
    return state


@traced_node("finalize_deployment")
def finalize_deployment_node(state: BuildState) -> BuildState:
    if not state.deployment_approved:
        state.deployment_status = "failed"
        update_deployment(state.deployment_id, status="failed")
        return state

    # Deploy step is the Cursor/Antigravity-owned CI/CD pipeline doing
    # the actual ship. Here we mark `live` once all merged tasks have
    # CI success; the external deploy workflow picks up from main.
    all_clean = all(t.status in ("merged",) for t in state.tasks)
    state.deployment_status = "live" if all_clean else "failed"
    state.rollback_url = f"https://github.com/{state.repo_full_name}/actions"
    update_deployment(
        state.deployment_id,
        status=state.deployment_status,
        rollback_url=state.rollback_url,
    )
    return state


@traced_node("deliver_client_docs")
def deliver_client_docs_node(state: BuildState) -> BuildState:
    """Client-mode only: produce Google Docs in a per-client Drive folder."""
    if state.mode != "client" or not state.deployment_approved:
        return state

    from .state import ClientDeliverable

    blueprint = state.scratch.get("blueprint") or {}
    proposal = blueprint.get("proposal") or {}

    state.drive_folder_id = ensure_client_drive_folder(state.client_slug, state.drive_folder_id)

    state.client_id = upsert_client_record(
        client_id=state.client_id,
        client_slug=state.client_slug,
        drive_folder_id=state.drive_folder_id,
        persona=blueprint.get("persona"),
        service_package=proposal.get("recommended_service_package"),
        account_id=blueprint.get("account_id"),
        opportunity_id=blueprint.get("opportunity_id"),
    )

    merged = [t for t in state.tasks if t.status == "merged"]
    deployment_summary = (
        f"Deployment {state.deployment_id} — status: {state.deployment_status}. "
        f"Tasks merged: {len(merged)}. PRs: "
        + ", ".join(t.pr_url for t in merged if t.pr_url)
    )

    doc_types = settings.agent("build_orchestrator")["client_deliverables"]["doc_types"]
    router = claude()
    delivered: list[ClientDeliverable] = []
    for dt in doc_types:
        md = author_client_doc(router, dt, state.client_slug, blueprint, deployment_summary)
        delivered.append(create_client_doc(state.drive_folder_id, state.client_slug, dt, md))
    state.deliverables = delivered
    log.info(
        "client_docs_delivered",
        client=state.client_slug,
        folder=state.drive_folder_id,
        ok=sum(1 for d in delivered if d.status == "created"),
        failed=sum(1 for d in delivered if d.status == "failed"),
    )
    return state


@traced_node("emit_deployment")
def emit_deployment_node(state: BuildState) -> BuildState:
    event = EventType.DEPLOYMENT_LIVE if state.deployment_status == "live" else EventType.DEPLOYMENT_FAILED
    emit_event(
        event,
        source_agent=state.agent_name,
        payload={
            "deployment_id": str(state.deployment_id),
            "blueprint_id": str(state.blueprint_id),
            "client_id": str(state.client_id) if state.client_id else None,
            "status": state.deployment_status,
            "task_count": len(state.tasks),
            "prs": [t.pr_url for t in state.tasks if t.pr_url],
        },
        correlation_id=state.correlation_id,
    )
    return state


def build():
    g = StateGraph(BuildState)
    g.add_node("load_deployment_tasks", load_deployment_tasks_node)
    g.add_node("dispatch_task_builds", dispatch_task_builds_node)
    g.add_node("build_single_task", build_single_task_node)
    g.add_node("collect_task_results", collect_task_results_node)
    g.add_node("hitl_gate", hitl_gate_node)
    g.add_node("merge_tasks", merge_tasks_node)
    g.add_node("finalize_deployment", finalize_deployment_node)
    g.add_node("deliver_client_docs", deliver_client_docs_node)
    g.add_node("emit_deployment", emit_deployment_node)

    g.set_entry_point("load_deployment_tasks")
    g.add_edge("load_deployment_tasks", "dispatch_task_builds")
    # Fan-out: one branch per task
    g.add_conditional_edges(
        "dispatch_task_builds",
        _dispatch_task_sends,
        ["build_single_task", "hitl_gate"],
    )
    # Fan-in
    g.add_edge("build_single_task", "collect_task_results")
    g.add_edge("collect_task_results", "hitl_gate")
    g.add_edge("hitl_gate", "merge_tasks")
    g.add_edge("merge_tasks", "finalize_deployment")
    g.add_edge("finalize_deployment", "deliver_client_docs")
    g.add_edge("deliver_client_docs", "emit_deployment")
    g.add_edge("emit_deployment", END)

    from omerion_core.runtime.checkpointer import get_checkpointer
    return g.compile(checkpointer=get_checkpointer())


