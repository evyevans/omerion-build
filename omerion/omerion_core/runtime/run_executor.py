"""Background run executor — AI execution primitive for agent runs.

**Wave 1 architectural note:** as of Wave 1.5, the canonical entry point
for every agent invocation is `agent_wrapper.run()`. The wrapper performs
the deterministic pre-AI checks (idempotency dedupe, mutex acquisition,
opt-out cohort filter, cost-budget pre-flight) and post-AI checks
(output schema validation, style-guard hard filter, recipient
verification, value-bound enforcement). It then delegates the actual
AI execution to this module.

This module remains the AI-execution primitive — it handles the
ThreadPoolExecutor wall-clock cap, the kill-switch gate, the lifecycle
state transitions, cost rollup, and HITL detection. New code should
**not call `execute_run` directly**; call `agent_wrapper.run()` and let
the wrapper invoke this internally.

Legacy callers still using `execute_run` directly:
  * `/agents/{name}/run` (control plane) — to migrate in Wave 1.9
  * `/inbound/discord/route` (Discord routing) — to migrate in Wave 1.9
  * `/hitl/resolve` re-entry after a paused graph approval (acceptable —
    HITL resume happens *inside* a wrapped run by definition)
  * `events/broker.py` per-event handoff dispatch — to migrate in Wave 2.7

Contract (unchanged):
  * Caller has already inserted an `agent_runs` row (status=queued) via
    `run_lifecycle.create_run(...)` and obtained `run_id`.
  * Caller schedules `execute_run(run_id)` on FastAPI BackgroundTasks (or
    any worker pool).
  * The executor flips the row to `running`, invokes the registered handler,
    then transitions to `completed` / `failed` / `hitl_waiting` based on the
    handler's return shape.

The session_id passed into the handler is set to `run_id` so the LangGraph
PostgresSaver thread_id ↔ run_id ↔ correlation_id are the same identifier.
"""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import traceback
from typing import Any
from uuid import UUID

from omerion_core.activity_logger import (
    log_hitl_waiting,
    log_run_complete,
    log_run_failed,
    log_run_start,
)
from omerion_core.clients.supabase_client import supabase
from omerion_core.exceptions import UserFacingError
from omerion_core.logging import get_logger
from omerion_core.runtime import run_lifecycle
import asyncio

from omerion_core.runtime.registry import run_agent_by_name, run_agent_by_name_async

# Patchable in tests to avoid 30-min waits.
AGENT_TIMEOUT_SECONDS: int = 1800
from omerion_core.settings import settings

log = get_logger("omerion.run_executor")


def _is_agent_paused(agent_name: str) -> tuple[bool, str | None]:
    """Check the agent_config kill switch. Returns (paused, reason).

    On lookup failure, fail OPEN (return not paused) — we'd rather miss an
    auto-pause than refuse to dispatch any agent if Supabase is having a bad
    minute. r4 will catch a missed pause on its next tick.
    """
    try:
        resp = (
            supabase.table("agent_config")
            .select("agent_schedule_enabled, paused_reason")
            .eq("agent_name", agent_name)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return False, None
        row = resp.data[0]
        if row.get("agent_schedule_enabled") is False:
            return True, row.get("paused_reason")
        return False, None
    except Exception as exc:  # noqa: BLE001
        log.warning("agent_config_lookup_failed", agent=agent_name, error=str(exc))
        return False, None


def _generate_friendly_summary_sync(agent_name: str, result: Any) -> str:
    """Use Claude to generate a short, punchy summary of the run's final state for the user."""
    if not isinstance(result, dict):
        if result is None:
            return ""
        return str(result)[:1000]


    from omerion_core.llm.router import claude, Tier
    
    prompt = (
        f"Agent '{agent_name}' just finished a task.\n\nFinal State:\n"
        f"{json.dumps(result, default=str)[:3000]}\n\n"
        "Write a short, punchy, user-friendly summary of what was accomplished and the key findings or results. "
        "Do not use code or JSON. Speak directly to the user in a helpful, conversational tone (max 2-3 sentences)."
    )
    system = "You are Omerion Control, a helpful AI assistant summarizing agent execution results for the user."
    try:
        resp = claude().complete(
            tier=Tier.FAST,
            system=system,
            prompt=prompt,
            max_tokens=200,
            temperature=0.3,
            agent_name=agent_name,
            node_name="result_summarizer"
        )
        if resp.get("text"):
            return resp["text"].strip()
    except Exception:
        pass
    
    # Fallback to the old JSON behavior if LLM fails
    try:
        return json.dumps(result, default=str)[:1000]
    except (TypeError, ValueError):
        return str(result)[:1000]


async def _generate_friendly_summary_async(agent_name: str, result: Any) -> str:
    """Async wrapper around the synchronous summary generation."""
    import asyncio
    return await asyncio.to_thread(_generate_friendly_summary_sync, agent_name, result)


def _run_cost_so_far(run_id: str) -> float:
    """Sum agent_telemetry.cost_usd for the in-flight run.

    Returns 0.0 on lookup failure — the watchdog must be tolerant of a flaky
    DB so a one-off Supabase blip cannot cause it to falsely supersede a run.
    """
    try:
        resp = (
            supabase.table("agent_telemetry")
            .select("cost_usd")
            .eq("run_id", run_id)
            .execute()
        )
        return float(sum(float(r.get("cost_usd") or 0.0) for r in (resp.data or [])))
    except Exception:  # noqa: BLE001
        return 0.0


def _start_cost_watchdog(run_id: str, agent_name: str, stop_event: threading.Event) -> threading.Thread:
    """Spawn a daemon thread that supersedes the run if cumulative cost ≥ cap×1.5.

    The completed-run cost check at the end of execute_run() catches overruns
    *after* the LLM has already burned tokens. This watchdog catches a runaway
    in-flight — it polls every 30s and triggers mark_superseded once the
    threshold is crossed. Combined with the superseded guards in transition()
    and resume_thread(), the orphan thread cannot revive the killed run.

    If per_run_cost_cap_usd is 0 (disabled), the thread exits immediately.
    """
    cap = settings.per_run_cost_cap_usd or 0.0
    if cap <= 0:
        # No cap configured — return a no-op thread to keep call-site symmetric.
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        return t

    threshold = cap * 1.5  # allow 50% headroom before killing
    interval = 30.0

    def _watch() -> None:
        # Small initial delay — most runs complete before this fires.
        if stop_event.wait(timeout=interval):
            return
        while not stop_event.is_set():
            spent = _run_cost_so_far(run_id)
            if spent >= threshold:
                log.error(
                    "cost_cap_exceeded_mid_flight",
                    run_id=run_id,
                    agent=agent_name,
                    spent_usd=spent,
                    cap_usd=cap,
                    threshold_usd=threshold,
                )
                try:
                    run_lifecycle.fail_run(
                        run_id,
                        error=f"Cost cap exceeded mid-flight: ${spent:.4f} ≥ ${threshold:.4f} (cap ${cap})",
                        llm_cost_usd=spent,
                    )
                    run_lifecycle.mark_superseded(run_id)
                except Exception as exc:  # noqa: BLE001
                    log.error("cost_cap_kill_failed", run_id=run_id, error=str(exc))
                return
            if stop_event.wait(timeout=interval):
                return

    t = threading.Thread(target=_watch, daemon=True, name=f"cost-watchdog-{run_id[:8]}")
    t.start()
    return t


def _extract_cost(result: Any) -> dict[str, Any]:
    """Pull cost rollup fields off the agent's final state, if present.

    Agents extending AgentRunState carry `tokens_input` / `tokens_output` /
    `cost_usd`; LangGraph returns the final state as a dict. We surface those
    at the run level so Mission Control doesn't have to join agent_telemetry.
    Missing fields are silently zero — agents that don't track cost still get
    a clean run record, just with no per-run dollar figure.
    """
    if not isinstance(result, dict):
        return {}
    return {
        "prompt_tokens": int(result.get("tokens_input") or 0),
        "completion_tokens": int(result.get("tokens_output") or 0),
        "llm_cost_usd": float(result.get("cost_usd") or 0.0),
    }


def execute_run(run_id: UUID | str) -> dict[str, Any]:
    """Execute the run identified by run_id and update its lifecycle row.

    Returns the final `agent_runs` row. Never raises — all exceptions are
    captured and persisted as `failed`. This matters because the executor
    runs in a BackgroundTasks worker where a raised exception would only be
    logged, not surfaced.
    """
    rid = str(run_id)
    row = run_lifecycle.get_run(rid)
    if row is None:
        log.error("execute_run_no_row", run_id=rid)
        return {}

    agent_name = row["agent_name"]

    # Kill-switch gate: r4 auto-pauses agents that breach critical thresholds.
    # Refuse to dispatch until the founder re-enables agent_schedule_enabled.
    paused, reason = _is_agent_paused(agent_name)
    if paused:
        log.warning("agent_run_skipped_paused", run_id=rid, agent=agent_name, reason=reason)
        skipped = run_lifecycle.fail_run(
            rid,
            error=f"agent_paused: {reason or 'r4 auto-pause; founder must re-enable in agent_config'}",
            llm_cost_usd=_run_cost_so_far(rid),
        )
        return skipped

    inputs = dict(row.get("inputs") or {})
    # One identifier across the entire stack: thread_id, session_id, run_id,
    # and correlation_id all equal `rid`. Agents that initialize their
    # AgentRunState from inputs pick these up; downstream telemetry, events,
    # HITL rows, and downstream telemetry join on the same UUID.
    inputs["session_id"] = rid
    inputs["run_id"] = rid
    inputs["correlation_id"] = row.get("correlation_id") or rid

    log.info("agent_run_starting", run_id=rid, agent=agent_name)
    run_lifecycle.mark_running(rid)
    log_run_start(agent_name, rid, triggered_by=row.get("triggered_by"))

    # Cost watchdog: polls agent_telemetry.cost_usd every 30s; supersedes the
    # run if cumulative spend exceeds per_run_cost_cap_usd × 1.5. Daemon thread,
    # signaled to stop the moment the main work finishes.
    cost_watchdog_stop = threading.Event()
    cost_watchdog = _start_cost_watchdog(rid, agent_name, cost_watchdog_stop)

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
            _future = _pool.submit(run_agent_by_name, agent_name, inputs)
            outcome = _future.result(timeout=1800)  # 30-minute wall-clock cap
    except concurrent.futures.TimeoutError:
        cost_watchdog_stop.set()
        log.error("agent_run_timeout", run_id=rid, agent=agent_name)
        timed_out = run_lifecycle.fail_run(
            rid,
            error="Execution timed out after 30 minutes",
            llm_cost_usd=_run_cost_so_far(rid),
        )
        # Python ThreadPoolExecutor cannot terminate the worker thread; it keeps
        # running until the graph completes naturally. mark_superseded() blocks
        # the orphan from later calling transition() to revive the run, and is
        # checked by checkpointer.resume_thread() to refuse stale resumes.
        run_lifecycle.mark_superseded(rid)
        log_run_failed(agent_name, rid, error="Execution timed out after 30 minutes")
        _notify_terminal(timed_out)
        return timed_out
    except UserFacingError as exc:
        # Designed-for-humans message — store only str(exc), no traceback in the
        # run row. The full traceback still reaches ops via log.exception().
        cost_watchdog_stop.set()
        log.exception("agent_run_user_facing_error", run_id=rid, agent=agent_name, message=str(exc))
        from omerion_core.telemetry.middleware import flush_telemetry
        flush_telemetry()
        failed = run_lifecycle.fail_run(
            rid,
            error=str(exc),
            llm_cost_usd=_run_cost_so_far(rid),
        )
        log_run_failed(agent_name, rid, error=str(exc))
        _notify_terminal(failed)
        return failed
    except Exception as exc:  # noqa: BLE001
        cost_watchdog_stop.set()
        tb = traceback.format_exc()
        log.error("agent_run_exception", run_id=rid, agent=agent_name, error=str(exc))
        from omerion_core.telemetry.middleware import flush_telemetry
        flush_telemetry()
        failed = run_lifecycle.fail_run(
            rid,
            error=f"{exc}\n\n{tb}",
            llm_cost_usd=_run_cost_so_far(rid),
        )
        log_run_failed(agent_name, rid, error=str(exc))
        _notify_terminal(failed)
        return failed
    finally:
        cost_watchdog_stop.set()

    status = outcome.get("status")
    if status == "hitl_pending":
        # The agent paused on an interrupt; the HITL row was already inserted
        # by the agent into founder_review_queue. Link it back if we can find
        # the matching pending review by session_id.
        review_id = _find_pending_review_id(rid)
        log.info("agent_run_hitl_waiting", run_id=rid, review_id=review_id)
        log_hitl_waiting(agent_name, rid, review_id=review_id)
        return run_lifecycle.mark_hitl_waiting(rid, review_id=review_id) if review_id \
            else run_lifecycle.transition(rid, "hitl_waiting")

    if status == "completed":
        result = outcome.get("result")
        summary = _generate_friendly_summary_sync(agent_name, result)
        cost = _extract_cost(result)
        log.info(
            "agent_run_completed",
            run_id=rid,
            agent=agent_name,
            prompt_tokens=cost.get("prompt_tokens", 0),
            completion_tokens=cost.get("completion_tokens", 0),
            llm_cost_usd=cost.get("llm_cost_usd", 0.0),
        )
        cap = settings.per_run_cost_cap_usd
        if cap > 0 and cost.get("llm_cost_usd", 0.0) > cap:
            log.error(
                "agent_run_cost_cap_exceeded",
                run_id=rid,
                agent=agent_name,
                llm_cost_usd=cost.get("llm_cost_usd", 0.0),
                cap_usd=cap,
            )
        duration_ms = None
        if row.get("started_at"):
            try:
                from omerion_core.util.time import parse_iso_utc
                started = parse_iso_utc(row["started_at"])
                if started:
                    from datetime import datetime as _dt, timezone as _tz
                    duration_ms = int((_dt.now(_tz.utc) - started).total_seconds() * 1000)
            except Exception:  # noqa: BLE001
                pass
        final = run_lifecycle.complete_run(rid, result_summary=summary, **cost)
        log_run_complete(
            agent_name, rid,
            duration_ms=duration_ms,
            cost_usd=cost.get("llm_cost_usd"),
            summary=summary,
        )
        _notify_terminal(final)
        return final

    # Unexpected handler shape — treat as failure rather than leaving
    # the run stuck in `running`.
    log.warning("agent_run_unknown_status", run_id=rid, outcome=outcome)
    final = run_lifecycle.fail_run(
        rid,
        error=f"unexpected handler outcome: {outcome!r}",
        llm_cost_usd=_run_cost_so_far(rid),
    )
    log_run_failed(agent_name, rid, error=f"unexpected handler outcome: {outcome!r}")
    _notify_terminal(final)
    return final


def _configure_langsmith() -> None:
    """Set LangChain tracing env vars from settings if LangSmith is configured.

    Called once at module import. LangGraph respects LANGCHAIN_TRACING_V2
    and LANGCHAIN_API_KEY automatically on every graph.invoke / ainvoke.
    """
    import os
    if settings.langsmith_api_key and settings.langchain_tracing_v2:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langsmith_project)
        log.info("langsmith_tracing_enabled", project=settings.langsmith_project)


_configure_langsmith()


async def execute_run_async(run_id: UUID | str) -> dict[str, Any]:
    """Async version of execute_run for use by AsyncIOScheduler job functions.

    Uses asyncio.wait_for + run_agent_by_name_async (graph.ainvoke) so the
    30-minute timeout propagates CancelledError into the coroutine at the next
    await point — cleanly terminating the graph — unlike ThreadPoolExecutor
    which cannot interrupt a running thread.

    The sync execute_run is kept for broker.py and agent_wrapper.py which run
    in non-async contexts (daemon threads). This async path is used by the
    scheduler, which runs inside the FastAPI event loop via AsyncIOScheduler.
    """
    rid = str(run_id)
    row = run_lifecycle.get_run(rid)
    if row is None:
        log.error("execute_run_no_row", run_id=rid)
        return {}

    agent_name = row["agent_name"]

    paused, reason = _is_agent_paused(agent_name)
    if paused:
        log.warning("agent_run_skipped_paused", run_id=rid, agent=agent_name, reason=reason)
        return run_lifecycle.fail_run(
            rid,
            error=f"agent_paused: {reason or 'r4 auto-pause; founder must re-enable in agent_config'}",
            llm_cost_usd=_run_cost_so_far(rid),
        )

    inputs = dict(row.get("inputs") or {})
    inputs["session_id"] = rid
    inputs["run_id"] = rid
    inputs["correlation_id"] = row.get("correlation_id") or rid

    log.info("agent_run_starting", run_id=rid, agent=agent_name)
    run_lifecycle.mark_running(rid)
    log_run_start(agent_name, rid, triggered_by=row.get("triggered_by"))

    async def _cost_watchdog_coro() -> None:
        cap = settings.per_run_cost_cap_usd or 0.0
        if cap <= 0:
            return
        threshold = cap * 1.5
        while True:
            await asyncio.sleep(30)
            spent = _run_cost_so_far(rid)
            if spent >= threshold:
                log.error("cost_cap_exceeded_mid_flight", run_id=rid, spent_usd=spent)
                try:
                    run_lifecycle.fail_run(rid, error=f"Cost cap: ${spent:.4f} ≥ ${threshold:.4f}", llm_cost_usd=spent)
                    run_lifecycle.mark_superseded(rid)
                except Exception as exc:
                    log.error("cost_cap_kill_failed", run_id=rid, error=str(exc))
                return

    cost_watchdog_task = asyncio.create_task(_cost_watchdog_coro())

    try:
        outcome = await asyncio.wait_for(
            run_agent_by_name_async(agent_name, inputs),
            timeout=AGENT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        cost_watchdog_task.cancel()
        log.error("agent_run_timeout", run_id=rid, agent=agent_name)
        timed_out = run_lifecycle.fail_run(
            rid,
            error=f"Execution timed out after {AGENT_TIMEOUT_SECONDS // 60} minutes",
            llm_cost_usd=_run_cost_so_far(rid),
        )
        run_lifecycle.mark_superseded(rid)
        log_run_failed(agent_name, rid, error="Execution timed out")
        _notify_terminal(timed_out)
        return timed_out
    except UserFacingError as exc:
        cost_watchdog_task.cancel()
        log.exception("agent_run_user_facing_error", run_id=rid, agent=agent_name, message=str(exc))
        from omerion_core.telemetry.middleware import flush_telemetry
        flush_telemetry()
        failed = run_lifecycle.fail_run(rid, error=str(exc), llm_cost_usd=_run_cost_so_far(rid))
        log_run_failed(agent_name, rid, error=str(exc))
        _notify_terminal(failed)
        return failed
    except Exception as exc:  # noqa: BLE001
        cost_watchdog_task.cancel()
        tb = traceback.format_exc()
        log.error("agent_run_exception", run_id=rid, agent=agent_name, error=str(exc))
        from omerion_core.telemetry.middleware import flush_telemetry
        flush_telemetry()
        failed = run_lifecycle.fail_run(rid, error=f"{exc}\n\n{tb}", llm_cost_usd=_run_cost_so_far(rid))
        log_run_failed(agent_name, rid, error=str(exc))
        _notify_terminal(failed)
        return failed
    finally:
        cost_watchdog_task.cancel()

    status = outcome.get("status")
    if status == "hitl_pending":
        review_id = _find_pending_review_id(rid)
        log.info("agent_run_hitl_waiting", run_id=rid, review_id=review_id)
        log_hitl_waiting(agent_name, rid, review_id=review_id)
        return run_lifecycle.mark_hitl_waiting(rid, review_id=review_id) if review_id \
            else run_lifecycle.transition(rid, "hitl_waiting")

    if status == "completed":
        result = outcome.get("result")
        summary = await _generate_friendly_summary_async(agent_name, result)
        cost = _extract_cost(result)
        log.info("agent_run_completed", run_id=rid, agent=agent_name, **cost)
        cap = settings.per_run_cost_cap_usd
        if cap > 0 and cost.get("llm_cost_usd", 0.0) > cap:
            log.error("agent_run_cost_cap_exceeded", run_id=rid, agent=agent_name, **cost, cap_usd=cap)
        final = run_lifecycle.complete_run(rid, result_summary=summary, **cost)
        log_run_complete(agent_name, rid, cost_usd=cost.get("llm_cost_usd"), summary=summary)
        _notify_terminal(final)
        return final

    log.warning("agent_run_unknown_status", run_id=rid, outcome=outcome)
    final = run_lifecycle.fail_run(rid, error=f"unexpected handler outcome: {outcome!r}", llm_cost_usd=_run_cost_so_far(rid))
    log_run_failed(agent_name, rid, error=f"unexpected handler outcome: {outcome!r}")
    _notify_terminal(final)
    return final


async def execute_resume(run_id: UUID | str, resume_payload: dict[str, Any]) -> dict[str, Any]:
    """Resume a paused HITL run via the LangGraph PostgresSaver.

    Called from `/hitl/resolve` as a BackgroundTasks job: the HTTP handler
    flips the agent_runs row to `running` and schedules this; we then drive
    the graph to its next interrupt or terminal state and update lifecycle
    + completion notification accordingly. Never raises.
    """
    from omerion_core.runtime.checkpointer import resume_thread

    rid = str(run_id)
    row = run_lifecycle.get_run(rid)
    if row is None:
        log.error("execute_resume_no_row", run_id=rid)
        return {}

    log.info("agent_run_resuming", run_id=rid, agent=row.get("agent_name"))

    try:
        result = await resume_thread(rid, resume_payload=resume_payload)
    except UserFacingError as exc:
        log.exception("agent_run_resume_user_facing_error", run_id=rid, message=str(exc))
        from omerion_core.telemetry.middleware import flush_telemetry
        flush_telemetry()
        failed = run_lifecycle.fail_run(
            rid,
            error=str(exc),
            llm_cost_usd=_run_cost_so_far(rid),
        )
        log_run_failed(row.get("agent_name", rid), rid, error=str(exc))
        _notify_terminal(failed)
        return failed
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error("agent_run_resume_exception", run_id=rid, error=str(exc))
        from omerion_core.telemetry.middleware import flush_telemetry
        flush_telemetry()
        failed = run_lifecycle.fail_run(
            rid,
            error=f"{exc}\n\n{tb}",
            llm_cost_usd=_run_cost_so_far(rid),
        )
        log_run_failed(row.get("agent_name", rid), rid, error=str(exc))
        _notify_terminal(failed)
        return failed

    # `resume_thread` raises if the graph hits another interrupt without a
    # value, but a normal completion returns the result dict.
    summary = await _generate_friendly_summary_async(row.get("agent_name", rid), result)
    cost = _extract_cost(result)
    log.info("agent_run_resumed_completed", run_id=rid, **cost)
    final = run_lifecycle.complete_run(rid, result_summary=summary, **cost)
    log_run_complete(row.get("agent_name", rid), rid, cost_usd=cost.get("llm_cost_usd"), summary=summary)
    _notify_terminal(final)
    return final


def _notify_terminal(run: dict[str, Any]) -> None:
    """Best-effort completion notification — never raises."""
    if not run:
        return
    try:
        from omerion_core.notifications.discord_webhook import post_run_completion
        post_run_completion(run)
    except Exception as exc:  # noqa: BLE001
        log.warning("notify_terminal_failed", run_id=run.get("run_id"), error=str(exc))


def _find_pending_review_id(run_id: str) -> str | None:
    """Look up the HITL review row the agent just inserted for this session."""
    try:
        from omerion_core.hitl.review import get_review_by_session
        review = get_review_by_session(run_id)
        return review["review_id"] if review else None
    except Exception as exc:  # noqa: BLE001
        log.warning("hitl_review_lookup_failed", run_id=run_id, error=str(exc))
        return None
