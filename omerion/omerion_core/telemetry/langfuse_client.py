"""Langfuse LLM observability client.

Provides a singleton Langfuse client that integrates with ClaudeRouter
to emit traces, spans, and cost metrics for every LLM call across all
7 Omerion sub-agents.

If LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY are not set, all calls
become no-ops — safe to deploy before Langfuse is live on the VPS.

Usage inside ClaudeRouter (already wired in router.py):

    from omerion_core.telemetry.langfuse_client import lf_generation

    with lf_generation(agent="intel", node="extract_w5h", model=model,
                       system=system, user=prompt) as gen:
        resp = self._complete(...)
        gen.end(output=resp["text"][:400], usage=resp["usage"])
"""
from __future__ import annotations

import contextlib
from contextlib import contextmanager
from typing import Any, Generator

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.telemetry.langfuse")

# ── Lazy singleton ────────────────────────────────────────────────────────────

_client: Any | None = None
_enabled: bool | None = None  # None = unchecked


def _is_enabled() -> bool:
    global _enabled
    if _enabled is None:
        _enabled = bool(
            settings.langfuse_secret_key and settings.langfuse_public_key
        )
        if not _enabled:
            log.info("langfuse_disabled", reason="missing LANGFUSE_SECRET_KEY / LANGFUSE_PUBLIC_KEY")
    return _enabled


def get_langfuse() -> Any | None:
    """Return the Langfuse client or None if unavailable."""
    global _client
    if _client is None and _is_enabled():
        try:
            from langfuse import Langfuse  # type: ignore[import]
            _client = Langfuse(
                secret_key=settings.langfuse_secret_key,
                public_key=settings.langfuse_public_key,
                host=settings.langfuse_host,
                flush_at=10,         # batch 10 events before HTTP flush
                flush_interval=30,   # flush at least every 30 s
            )
            log.info("langfuse_connected", host=settings.langfuse_host)
        except Exception as exc:
            log.warning("langfuse_init_failed", error=str(exc))
            _enabled = False
    return _client


# ── Trace helpers ─────────────────────────────────────────────────────────────

class _NoopGeneration:
    """Stand-in when Langfuse is disabled — all calls are safe no-ops."""
    def end(self, **_: Any) -> None: ...
    def update(self, **_: Any) -> None: ...


@contextmanager
def lf_generation(
    *,
    agent: str,
    node: str,
    model: str,
    system: str | list | None = None,
    user: str | None = None,
    session_id: str | None = None,
) -> Generator[Any, None, None]:
    """Context manager that wraps one LLM call with a Langfuse generation span.

    Args:
        agent: Agent codename (e.g. "intel", "match", "nurture")
        node:  LangGraph node name (e.g. "extract_w5h", "propose_node")
        model: Claude model string used for this call
        system: System prompt (first 300 chars captured)
        user:   User prompt (first 300 chars captured)
        session_id: Optional session UUID for trace grouping

    Yields a generation object with .end() and .update() — no-op if disabled.
    """
    lf = get_langfuse()
    if lf is None:
        yield _NoopGeneration()
        return

    # Truncate prompts to avoid huge Langfuse payloads
    sys_preview = (
        (system[:300] if isinstance(system, str) else str(system)[:300])
        if system else None
    )
    usr_preview = user[:300] if user else None

    trace = lf.trace(
        name=f"{agent}.{node}",
        session_id=session_id,
        tags=[agent, "omerion"],
    )
    generation = trace.generation(
        name=node,
        model=model,
        model_parameters={"temperature": 0.2},
        input={"system": sys_preview, "user": usr_preview},
    )
    try:
        yield generation
    except Exception as exc:
        with contextlib.suppress(Exception):
            generation.update(level="ERROR", status_message=str(exc))
        raise
    # .end() is called by the caller with output + usage


def lf_flush() -> None:
    """Force-flush pending Langfuse events. Call on app shutdown."""
    lf = get_langfuse()
    if lf is not None:
        with contextlib.suppress(Exception):
            lf.flush()
