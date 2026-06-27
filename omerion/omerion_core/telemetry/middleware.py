"""Telemetry — writes every node execution to `agent_telemetry`.

Usage from a LangGraph node:

    @traced_node("extract_w5h")
    def node_extract_w5h(state: MeetingIntelState) -> MeetingIntelState:
        ...
"""
from __future__ import annotations

import time
import traceback
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, TypeVar
from uuid import UUID

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.telemetry")

T = TypeVar("T")


class TelemetryMiddleware:
    """Flush-oriented buffer. Nodes write rows; middleware batches them."""

    def __init__(self, flush_batch_size: int = 50) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._flush_batch_size = flush_batch_size

    def record(self, row: dict[str, Any]) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self._flush_batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        rows = self._buffer[:]
        self._buffer.clear()
        try:
            supabase.table("agent_telemetry").insert(rows).execute()
        except Exception as e:
            log.error("telemetry_flush_failed", error=str(e), rows=len(rows))


_middleware = TelemetryMiddleware()


def _coerce_uuid(v: Any) -> str | None:
    if v is None:
        return None
    return str(v) if isinstance(v, UUID) else str(v)


@contextmanager
def _node_span(agent_name: str, session_id: str, run_id: UUID | str, node_name: str,
               model_used: str | None = None, correlation_id: UUID | str | None = None):
    started = time.time()
    row: dict[str, Any] = {
        "agent_name": agent_name,
        "session_id": session_id,
        "run_id": _coerce_uuid(run_id),
        "correlation_id": _coerce_uuid(correlation_id),
        "node_name": node_name,
        "started_at": _iso(started),
        "status": "success",
        "tokens_input": 0,
        "tokens_output": 0,
        "cost_usd": 0.0,
        "model_used": model_used,
        "hitl_wait_ms": 0,
    }
    try:
        yield row
    except Exception as exc:
        row["status"] = "failure"
        row["error"] = {
            "class": type(exc).__name__,
            "message": str(exc),
            "stack": traceback.format_exc(limit=6),
        }
        raise
    finally:
        ended = time.time()
        row["ended_at"] = _iso(ended)
        row["duration_ms"] = int((ended - started) * 1000)
        _middleware.record(row)


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def traced_node(node_name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator — wraps a LangGraph node function for telemetry.

    Assumes the first positional arg is a state instance carrying at least
    `agent_name`, `session_id`, and `run_id`.
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(state, *args, **kwargs):
            with _node_span(
                agent_name=getattr(state, "agent_name"),
                session_id=getattr(state, "session_id"),
                run_id=getattr(state, "run_id"),
                correlation_id=getattr(state, "correlation_id", None),
                node_name=node_name,
            ) as row:
                new_state = fn(state, *args, **kwargs)
                # Capture incremental cost delta between calls
                row["tokens_input"] = int(getattr(new_state, "tokens_input", 0)) - int(getattr(state, "tokens_input", 0))
                row["tokens_output"] = int(getattr(new_state, "tokens_output", 0)) - int(getattr(state, "tokens_output", 0))
                row["cost_usd"] = float(getattr(new_state, "cost_usd", 0.0)) - float(getattr(state, "cost_usd", 0.0))
                state.record_node(node_name) if hasattr(state, "record_node") else None
                return new_state
        return wrapper
    return decorator


def flush_telemetry() -> None:
    _middleware.flush()
