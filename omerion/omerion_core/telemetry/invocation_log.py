"""Best-effort logger for prompt_invocations rows.

Called by `ClaudeRouter.complete()` after every successful LLM call.
Never raises — a Supabase outage MUST NOT block the original LLM
call from returning to the caller. Errors are logged at WARN.

Design notes:
  * Text fields are capped at 8 KB. Anything longer gets head+tail
    so shadow-eval replay still has the start/end of the prompt
    without DB-row size blowing up.
  * `success` and `error_class` are NULL at insert time. The wrapper
    backfills them via `mark_invocation_outcome()` after node
    post-validation runs.
  * `invocation_id` is returned so the wrapper can backfill the
    correct row even when multiple LLM calls happen in one node.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.telemetry.invocation_log")

# Storage cap per text column. Above this we keep head + ellipsis + tail.
_MAX_TEXT_BYTES = 8000
_MAX_ERROR_MESSAGE_BYTES = 2000


def _truncate(text: str | None, max_bytes: int = _MAX_TEXT_BYTES) -> str | None:
    """Cap long text with a head+tail marker so the head and tail are
    preserved (shadow eval cares about both). Bytes, not chars, because
    Supabase TEXT column limits are byte-based."""
    if text is None:
        return None
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    head_bytes = (max_bytes - 50) // 2
    tail_bytes = (max_bytes - 50) - head_bytes
    head = encoded[:head_bytes].decode("utf-8", errors="replace")
    tail = encoded[-tail_bytes:].decode("utf-8", errors="replace")
    return (
        f"{head}\n…[truncated {len(encoded) - max_bytes + 50} bytes]…\n{tail}"
    )


def _sha256(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def log_invocation(
    *,
    agent_name: str | None,
    node_name: str | None,
    prompt_constant_name: str | None,
    system_text: str | None,
    user_text: str | None,
    response_text: str | None,
    model: str,
    tier: str | None,
    tokens_in: int,
    tokens_out: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: float,
    latency_ms: int | None = None,
    run_id: UUID | str | None = None,
    correlation_id: UUID | str | None = None,
    inputs_redacted: bool = False,
) -> UUID | None:
    """Insert a prompt_invocations row. Returns invocation_id, or None on failure.

    Never raises. A Supabase outage results in a WARN log and a None
    return — the caller continues as if logging succeeded.
    """
    invocation_id = uuid4()
    row: dict[str, Any] = {
        "invocation_id": str(invocation_id),
        "run_id": str(run_id) if run_id else None,
        "correlation_id": str(correlation_id) if correlation_id else None,
        "agent_name": agent_name or "unknown",
        "node_name": node_name or "llm_call",
        "prompt_constant_name": prompt_constant_name,
        "prompt_sha256": _sha256(system_text),
        "model": model,
        "tier": tier,
        "rendered_input_hash": _sha256(user_text),
        "rendered_input_text": _truncate(user_text),
        "response_text": _truncate(response_text),
        "inputs_redacted": inputs_redacted,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cache_read_tokens": int(cache_read_tokens or 0),
        "cache_write_tokens": int(cache_write_tokens or 0),
        "cost_usd": float(cost_usd or 0.0),
        "latency_ms": latency_ms,
        # success/error_class deliberately omitted — backfilled by the
        # wrapper after node post-validation.
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("prompt_invocations").insert(row).execute()
        return invocation_id
    except Exception as exc:  # noqa: BLE001 — never block the LLM call
        log.warning(
            "invocation_log_insert_failed",
            agent=agent_name,
            constant=prompt_constant_name,
            error=str(exc),
        )
        return None


def mark_invocation_outcome(
    invocation_id: UUID | str,
    *,
    success: bool,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    """Backfill `success` / `error_class` / `error_message` after node
    post-validation runs. Wrapper calls this once per invocation.

    Never raises. If the row doesn't exist (e.g., earlier insert failed),
    this is silently a no-op.
    """
    update: dict[str, Any] = {"success": success}
    if error_class is not None:
        update["error_class"] = error_class[:80]
    if error_message is not None:
        update["error_message"] = _truncate(error_message, _MAX_ERROR_MESSAGE_BYTES)
    try:
        (
            supabase.table("prompt_invocations")
            .update(update)
            .eq("invocation_id", str(invocation_id))
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "invocation_log_mark_outcome_failed",
            invocation_id=str(invocation_id),
            error=str(exc),
        )


__all__ = ["log_invocation", "mark_invocation_outcome"]
