"""Skill → handler registry.

Each of the 14 .skill.md files maps to exactly one handler. A handler is a
small adapter that accepts a payload dict and starts the right pipeline:

  * LangGraph graphs (#7/#8/#9) — compiled with the shared PostgresSaver,
    invoked with a `thread_id` so pause/resume works.
  * Claude Agent SDK handlers (#1-6, #10) — in-process; no checkpointer.
  * Claude Managed Agents (R1-R4) — cloud-side; handler wraps the
    `anthropic` SDK call with the managed-agents-2026-04-01 beta header.

Handlers register themselves at import time. The FastAPI entrypoint
(`main.py`) imports `omerion.agents` which triggers all registrations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable  # noqa: F401

from omerion_core.logging import get_logger

log = get_logger("omerion.runtime.registry")

SkillCallable = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class SkillHandler:
    name: str                    # kebab-case matches .skill.md filename
    runtime: str                 # "langgraph" | "agent_sdk" | "managed_agent"
    handler: SkillCallable


_registry: dict[str, SkillHandler] = {}


def register(name: str, *, runtime: str, handler: SkillCallable) -> None:
    if runtime not in {"langgraph", "agent_sdk", "managed_agent"}:
        raise ValueError(f"unknown runtime: {runtime}")
    if name in _registry:
        log.warning("skill_registration_overwrite", skill=name, runtime=runtime)
    _registry[name] = SkillHandler(name=name, runtime=runtime, handler=handler)
    log.info("skill_registered", skill=name, runtime=runtime)


def get_handler(name: str) -> SkillHandler:
    if name not in _registry:
        raise KeyError(f"skill not registered: {name}")
    return _registry[name]


def registered_skills() -> list[SkillHandler]:
    return list(_registry.values())


def run_agent_by_name(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Invoke a skill by its kebab-case name and return a result dict.

    For LangGraph handlers, the compiled graph is the handler itself.
    We generate a thread_id from the session_id in inputs (or mint a new one)
    so the checkpointer can persist state across HITL pauses.
    """
    import uuid
    from langgraph.types import Command  # noqa: F401 — ensure langgraph is importable

    from omerion_core.runtime.event_ingress import map_event_payload_to_state

    handler = get_handler(name)
    session_id = inputs.get("session_id") or str(uuid.uuid4())
    inputs.setdefault("session_id", session_id)
    inputs = map_event_payload_to_state(name, inputs)

    if handler.runtime == "langgraph":
        # Explicit recursion_limit (LangGraph default is 25). A graph that exceeds
        # it raises GraphRecursionError, caught cleanly by execute_run → failed run.
        config = {"configurable": {"thread_id": session_id}, "recursion_limit": 50}
        try:
            result = handler.handler.invoke(inputs, config=config)
        except Exception as exc:  # noqa: BLE001
            from langgraph.errors import GraphInterrupt
            if isinstance(exc, GraphInterrupt):
                log.info("agent_hitl_interrupted", skill=name, session_id=session_id)
                return {"session_id": session_id, "status": "hitl_pending"}
            raise
        return {"session_id": session_id, "status": "completed", "result": result}

    # agent_sdk / managed_agent — handler is a plain callable
    result = handler.handler(inputs)
    return {"session_id": session_id, "status": "completed", "result": result}


async def run_agent_by_name_async(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Async version of run_agent_by_name for use with asyncio.wait_for + ainvoke.

    LangGraph handlers use graph.ainvoke() so the timeout propagates as CancelledError
    into the coroutine at the next await point — unlike ThreadPoolExecutor which
    cannot interrupt a running thread.

    agent_sdk / managed_agent handlers (plain callables) run in a thread pool via
    asyncio.to_thread so they don't block the event loop.
    """
    import asyncio
    import uuid

    from omerion_core.runtime.event_ingress import map_event_payload_to_state

    handler = get_handler(name)
    session_id = inputs.get("session_id") or str(uuid.uuid4())
    inputs.setdefault("session_id", session_id)
    inputs = map_event_payload_to_state(name, inputs)

    if handler.runtime == "langgraph":
        config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": 50,  # explicit (LangGraph default 25); GraphRecursionError → clean fail
            "run_name": f"{name}:{session_id[:8]}",
            "tags": [name, "production"],
            "metadata": {"agent_id": name, "run_id": session_id, "session_id": session_id},
        }
        try:
            result = await handler.handler.ainvoke(inputs, config=config)
        except Exception as exc:  # noqa: BLE001
            from langgraph.errors import GraphInterrupt
            if isinstance(exc, GraphInterrupt):
                log.info("agent_hitl_interrupted", skill=name, session_id=session_id)
                return {"session_id": session_id, "status": "hitl_pending"}
            raise
        return {"session_id": session_id, "status": "completed", "result": result}

    # agent_sdk / managed_agent — run sync callable off the event loop
    result = await asyncio.to_thread(handler.handler, inputs)
    return {"session_id": session_id, "status": "completed", "result": result}
