"""Base LangGraph state model shared by every agent.

Each agent extends `AgentRunState` with its own fields. The base provides
the invariants the telemetry and HITL layers rely on (run_id, agent_name,
session_id, trace of nodes visited, hitl status, cost accumulators).
"""
from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class HitlStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AgentRunState(BaseModel):
    """Base state — subclass per agent and add domain fields."""

    run_id: UUID = Field(default_factory=uuid4)
    agent_name: str
    session_id: str = "local"               # Discord session ID; "local" for offline/direct runs
    correlation_id: UUID = Field(default_factory=uuid4)
    # Free-text prompt when the run was triggered from Discord. event_ingress
    # copies it into state for every run; an agent's parse_discord_intent node
    # reads it to target the request. None on event/cron runs (pass-through).
    discord_message: str | None = None

    # ─── Tracing ────────────────────────────────────────────────
    nodes_visited: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)

    # ─── Cost / telemetry rollup ────────────────────────────────
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    hitl_wait_ms: int = 0

    # ─── HITL ───────────────────────────────────────────────────
    hitl_status: HitlStatus = HitlStatus.NONE
    hitl_review_id: UUID | None = None
    hitl_regen_attempts: int = 0

    # ─── Arbitrary scratch space ────────────────────────────────
    scratch: dict[str, Any] = Field(default_factory=dict)

    def record_node(self, name: str) -> None:
        self.nodes_visited.append(name)

    def record_llm(self, usage: dict[str, int], cost_usd: float) -> None:
        self.tokens_input += int(usage.get("input_tokens", 0))
        self.tokens_output += int(usage.get("output_tokens", 0))
        self.cost_usd += cost_usd

    def record_error(self, node: str, exc: Exception) -> None:
        self.errors.append({"node": node, "class": type(exc).__name__, "message": str(exc)})
