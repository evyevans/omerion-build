"""Shared LangGraph AsyncPostgresSaver for complex HITL pipelines.

Used by agents #7 (offer_matching), #8 (meeting_intelligence), and
#9 (build_orchestrator). The checkpointer persists every graph step into
Supabase (via DATABASE_URL — direct connection, not the pooler) so a
paused `interrupt(...)` node can survive process restarts.

Resume path:
    inbound/hitl.py  →  execute_resume(run_id, payload)
        →  resume_thread(thread_id, resume_payload={...})
            which calls graph.ainvoke(Command(resume=payload), config={...})
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.runtime.checkpointer")

_pool = None
_saver = None  # AsyncPostgresSaver | None


async def setup_checkpointer() -> None:
    """Initialize async checkpointer. Call once from FastAPI lifespan."""
    global _pool, _saver

    if not settings.database_url:
        log.warning("checkpointer_disabled_no_database_url")
        return

    if getattr(settings, "omerion_env", None) == "dev":
        log.warning("checkpointer_disabled_in_dev_env")
        return

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    db_url = settings.database_url
    parsed = urlparse(db_url)
    qs = parse_qs(parsed.query)
    qs.setdefault("connect_timeout", ["5"])
    fast_url = urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

    pool = AsyncConnectionPool(
        conninfo=fast_url,
        min_size=0,
        max_size=10,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
        },
    )

    try:
        await asyncio.wait_for(pool.open(), timeout=8)
    except asyncio.TimeoutError:
        log.warning("checkpointer_setup_timeout — DB unreachable; checkpointer disabled")
        return
    except Exception as exc:
        log.error("checkpointer_pool_open_failed", error=str(exc))
        return

    saver = AsyncPostgresSaver(pool)
    try:
        await asyncio.wait_for(saver.setup(), timeout=8)
    except asyncio.TimeoutError:
        log.warning("checkpointer_schema_timeout — setup() stalled; checkpointer disabled")
        await pool.close()
        return
    except Exception as exc:
        log.error("checkpointer_setup_failed", error=str(exc))
        await pool.close()
        return

    _pool = pool
    _saver = saver
    log.info("checkpointer_ready")


async def teardown_checkpointer() -> None:
    """Close async connection pool. Call from FastAPI lifespan shutdown."""
    global _pool, _saver
    if _pool is not None:
        try:
            await _pool.close()
        except Exception as exc:
            log.warning("checkpointer_pool_close_error", error=str(exc))
    _pool = None
    _saver = None


def get_checkpointer():  # type: ignore[return]
    """Return the initialized AsyncPostgresSaver (or None if disabled)."""
    return _saver


async def cancel_thread(thread_id: str) -> bool:
    """Return True if a checkpoint exists for thread_id, False otherwise.

    Does not interrupt an in-flight graph invocation. The caller is responsible
    for transitioning the agent_runs row to 'cancelled'.
    """
    saver = get_checkpointer()
    if saver is None:
        return False
    config = {"configurable": {"thread_id": thread_id}}
    return await saver.aget(config) is not None


async def resume_thread(thread_id: str, *, resume_payload: dict[str, Any]) -> dict[str, Any]:
    """Resume a paused LangGraph thread with a HITL decision payload."""
    from langgraph.types import Command

    saver = get_checkpointer()
    if saver is None:
        raise RuntimeError(
            "DATABASE_URL not configured or checkpointer disabled — cannot resume LangGraph threads"
        )

    # Superseded guard: if the executor's timeout branch marked this run dead,
    # refuse to resume. Wrap in to_thread so the sync Supabase client doesn't
    # block the event loop.
    from omerion_core.clients.supabase_client import supabase

    run_row = await asyncio.to_thread(
        lambda: (
            supabase.table("agent_runs")
            .select("superseded_at,status")
            .eq("run_id", thread_id)
            .limit(1)
            .execute()
        )
    )
    if run_row.data and run_row.data[0].get("superseded_at"):
        raise RuntimeError(
            f"thread {thread_id} was superseded ({run_row.data[0].get('status')}); refusing resume"
        )

    config = {"configurable": {"thread_id": thread_id}}
    state = await saver.aget(config)
    if state is None:
        raise LookupError(f"no checkpoint found for thread_id={thread_id}")

    skill = state.values.get("skill") if hasattr(state, "values") else None
    if not skill:
        raise LookupError(f"thread {thread_id} has no skill marker in state")

    from omerion_core.runtime.registry import get_handler

    graph = get_handler(skill).handler  # compiled graph stored as the handler
    result = await graph.ainvoke(Command(resume=resume_payload), config=config)
    log.info("thread_resumed", thread_id=thread_id, skill=skill)
    return {
        "thread_id": thread_id,
        "skill": skill,
        "result_keys": list(result.keys()) if isinstance(result, dict) else [],
    }


def _expired_thread_ids(retention_days: int) -> list[str]:
    """Thread IDs of terminal (non-HITL) runs older than retention_days.

    CRITICAL: the status filter is terminal-only — `hitl_waiting` runs are
    excluded, so checkpoints needed to resume a paused graph on HITL approval
    are never deleted.
    """
    from datetime import datetime, timedelta, timezone

    from omerion_core.clients.supabase_client import supabase

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    try:
        expired_runs = (
            supabase.table("agent_runs")
            .select("thread_id")
            .in_("status", ["completed", "failed", "cancelled", "superseded"])
            .lt("updated_at", cutoff)
            .not_.is_("thread_id", "null")
            .limit(500)
            .execute()
        )
    except Exception as exc:
        log.error("checkpoint_cleanup_query_failed", error=str(exc))
        return []
    return [r["thread_id"] for r in (expired_runs.data or []) if r.get("thread_id")]


async def _delete_checkpoints(thread_ids: list[str]) -> None:
    async with _pool.connection() as conn:
        await conn.execute(
            "DELETE FROM checkpoint_blobs WHERE thread_id = ANY(%s)", (thread_ids,)
        )
        await conn.execute(
            "DELETE FROM checkpoint_writes WHERE thread_id = ANY(%s)", (thread_ids,)
        )
        await conn.execute(
            "DELETE FROM checkpoints WHERE thread_id = ANY(%s)", (thread_ids,)
        )


async def cleanup_expired_checkpoints_async(*, retention_days: int = 30) -> dict[str, int]:
    """Async-native checkpoint TTL cleanup — safe to `await` from AsyncIOScheduler.

    Replaces the broken sync path that called `asyncio.get_event_loop()
    .run_until_complete()` from inside the already-running scheduler loop. That
    raised `RuntimeError: event loop is already running` on EVERY nightly tick,
    was swallowed as a warning, and the delete never ran → checkpoints /
    checkpoint_blobs / checkpoint_writes grew unbounded. CRITICAL: never deletes
    `hitl_waiting` runs (see `_expired_thread_ids`).
    """
    thread_ids = _expired_thread_ids(retention_days)
    if not thread_ids:
        log.info("checkpoint_cleanup_nothing_to_delete", retention_days=retention_days)
        return {"deleted_threads": 0}
    if _pool is None:
        log.warning("checkpoint_cleanup_skipped_no_pool")
        return {"deleted_threads": 0}
    try:
        await _delete_checkpoints(thread_ids)
    except Exception as exc:  # noqa: BLE001
        log.error("checkpoint_cleanup_delete_failed", error=str(exc))
        return {"deleted_threads": 0}
    log.info("checkpoint_cleanup_complete", deleted_threads=len(thread_ids), retention_days=retention_days)
    return {"deleted_threads": len(thread_ids)}


def cleanup_expired_checkpoints(*, retention_days: int = 30) -> dict[str, int]:
    """Sync entry point for non-async callers (manual scripts / tests).

    Delegates to the async implementation via `asyncio.run()`. Do NOT call this
    from inside a running event loop — the scheduler uses
    `cleanup_expired_checkpoints_async()` directly.
    """
    import asyncio

    try:
        return asyncio.run(cleanup_expired_checkpoints_async(retention_days=retention_days))
    except RuntimeError as exc:
        log.warning(
            "checkpoint_cleanup_sync_called_in_running_loop",
            hint="use cleanup_expired_checkpoints_async()",
            error=str(exc),
        )
        return {"deleted_threads": 0}
