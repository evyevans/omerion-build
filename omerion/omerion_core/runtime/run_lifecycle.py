"""Agent run lifecycle — durable state for every graph execution.

The `agent_runs` table is the single source of truth for "is this run still
going? did it succeed?". Graph execution state continues to live in
`checkpoints` (LangGraph PostgresSaver); per-node spans in `agent_telemetry`.
This module is a thin CRUD layer over `agent_runs` so the run executor,
control plane, Discord route, and HITL resume path all transition state the
same way.

State machine:
    queued -> running -> { completed | failed | cancelled }
                  └---> hitl_waiting -> running -> { completed | failed }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.run_lifecycle")


VALID_STATUSES = {
    "queued",
    "running",
    "hitl_waiting",
    "completed",
    "failed",
    "cancelled",
}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_run(
    *,
    agent_name: str,
    source_channel: str,
    inputs: dict[str, Any] | None = None,
    triggered_by: str | None = None,
    discord_channel_id: str | None = None,
    discord_thread_id: str | None = None,
    correlation_id: UUID | str | None = None,
    run_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Insert a new run row in `queued` state. Returns the inserted row.

    `thread_id` is set to `run_id::text` so the LangGraph PostgresSaver
    checkpoints for this run are addressable by run_id.
    """
    rid = str(run_id) if run_id else str(uuid4())
    corr = str(correlation_id) if correlation_id else rid  # default: run_id == correlation_id
    row = {
        "run_id": rid,
        "agent_name": agent_name,
        "thread_id": rid,
        "status": "queued",
        "source_channel": source_channel,
        "triggered_by": triggered_by,
        "inputs": inputs or {},
        "discord_channel_id": discord_channel_id,
        "discord_thread_id": discord_thread_id,
        "correlation_id": corr,
    }
    resp = supabase.table("agent_runs").insert(row).execute()
    log.info(
        "agent_run_created",
        run_id=rid,
        agent=agent_name,
        source_channel=source_channel,
        correlation_id=corr,
    )
    return resp.data[0]


def get_run(run_id: UUID | str) -> dict[str, Any] | None:
    resp = (
        supabase.table("agent_runs")
        .select("*")
        .eq("run_id", str(run_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def list_runs(
    *,
    status: str | None = None,
    agent_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = supabase.table("agent_runs").select("*").order("created_at", desc=True).limit(limit)
    if status:
        q = q.eq("status", status)
    if agent_name:
        q = q.eq("agent_name", agent_name)
    return q.execute().data or []


def transition(
    run_id: UUID | str,
    new_status: str,
    *,
    review_id: UUID | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update status and (optionally) link a HITL review row.

    Idempotent at the terminal boundary: a transition into the same terminal
    status is a no-op rather than an error.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {new_status}")

    current = get_run(run_id)
    if current is None:
        raise ValueError(f"run not found: {run_id}")
    if current["status"] in TERMINAL_STATUSES and current["status"] == new_status:
        return current

    # Superseded guard: a run flagged superseded (e.g. by the executor's
    # timeout branch) is logically dead. The orphan thread may still call
    # transition() with stale state — block it unless the target is itself
    # terminal (we allow re-asserting a terminal status as a no-op safety net).
    if current.get("superseded_at") and new_status not in TERMINAL_STATUSES:
        log.warning(
            "transition_blocked_superseded",
            run_id=str(run_id),
            attempted_status=new_status,
            current_status=current["status"],
        )
        return current

    update: dict[str, Any] = {"status": new_status}
    if review_id is not None:
        update["review_id"] = str(review_id)
    if new_status == "running" and not current.get("started_at"):
        update["started_at"] = _now_iso()
    if new_status in TERMINAL_STATUSES:
        update["finished_at"] = _now_iso()
    if extra:
        update.update(extra)

    supabase.table("agent_runs").update(update).eq("run_id", str(run_id)).execute()
    log.info(
        "agent_run_transitioned",
        run_id=str(run_id),
        from_status=current["status"],
        to_status=new_status,
    )
    try:
        supabase.table("state_change_log").insert({
            "run_id": str(run_id),
            "agent_name": current.get("agent_name", ""),
            "from_status": current["status"],
            "to_status": new_status,
            "meta": extra or {},
        }).execute()
    except Exception as _audit_err:
        log.warning("state_change_log insert failed", error=str(_audit_err))
    return get_run(run_id) or {}


def mark_running(run_id: UUID | str) -> dict[str, Any]:
    return transition(run_id, "running")


def mark_hitl_waiting(
    run_id: UUID | str,
    review_id: UUID | str | None = None,
    *,
    hitl_expires_at: str | None = None,
) -> dict[str, Any]:
    extra = {}
    if hitl_expires_at:
        extra["hitl_expires_at"] = hitl_expires_at
    return transition(run_id, "hitl_waiting", review_id=review_id, extra=extra or None)


def complete_run(
    run_id: UUID | str,
    *,
    result_summary: str | None = None,
    cost_usd: float | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    llm_cost_usd: float | None = None,
    tool_call_count: int | None = None,
) -> dict[str, Any]:
    """Mark a run completed and (optionally) stamp its final cost numbers.

    Why these split out from `cost_usd`: prior `cost_usd` was a single rollup;
    Phase D cost accounting wants tokens-in / tokens-out / dollar cost / tool
    call count separately so the Mission Control view can render per-run
    profitability without needing to join agent_telemetry.
    """
    extra: dict[str, Any] = {}
    if result_summary is not None:
        extra["result_summary"] = result_summary
    if cost_usd is not None:
        extra["cost_usd"] = cost_usd
    if prompt_tokens is not None:
        extra["prompt_tokens"] = int(prompt_tokens)
    if completion_tokens is not None:
        extra["completion_tokens"] = int(completion_tokens)
    if llm_cost_usd is not None:
        extra["llm_cost_usd"] = float(llm_cost_usd)
    if tool_call_count is not None:
        extra["tool_call_count"] = int(tool_call_count)
    return transition(run_id, "completed", extra=extra)


def fail_run(
    run_id: UUID | str,
    *,
    error: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    llm_cost_usd: float = 0.0,
) -> dict[str, Any]:
    # Failed runs still consumed tokens before they crashed — record what we
    # know so the spend calendar / per-agent rollup doesn't show $0 for runs
    # that genuinely cost money. Only stamp non-zero values: a follow-up call
    # with all-zeros (e.g. a paused-skip with no telemetry) must not blank
    # out cost numbers a watchdog / mid-flight transition already wrote.
    extra: dict[str, Any] = {"error": error[:4000]}
    if prompt_tokens:
        extra["prompt_tokens"] = int(prompt_tokens)
    if completion_tokens:
        extra["completion_tokens"] = int(completion_tokens)
    if llm_cost_usd:
        extra["llm_cost_usd"] = float(llm_cost_usd)
    return transition(run_id, "failed", extra=extra)


def mark_superseded(run_id: UUID | str) -> None:
    """Flag a run as logically dead — late writes from orphan threads will be refused.

    Used by the executor's timeout branch: after fail_run() marks the row
    failed, we stamp superseded_at so the orphan ThreadPoolExecutor thread
    (which Python cannot kill) is unable to revive the run via a later
    transition() call. This bypasses the superseded guard in transition()
    by writing the column directly — only callers that have authority to
    declare a run dead should use this.
    """
    supabase.table("agent_runs").update(
        {"superseded_at": _now_iso()}
    ).eq("run_id", str(run_id)).execute()
    log.warning("run_marked_superseded", run_id=str(run_id))


def cancel_run(run_id: UUID | str, *, reason: str | None = None) -> dict[str, Any]:
    extra = {"error": f"cancelled: {reason}"} if reason else {}
    return transition(run_id, "cancelled", extra=extra)
