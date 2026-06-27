"""State for TRAINER (Agent #16).

Three nested models flow through the graph:

  * `AgentPerformance` — one row per target agent, aggregated over the
    7-day window. Fills `state.performance_summaries` from Node 1.
  * `UnderperformingAgent` — the subset of agents that crossed
    deterministic thresholds in Node 2. Carries a snapshot of the
    agent's current prompt constants (AST-parsed, never exec'd).
  * `PromptProposal` — the LLM's rewritten prompt for one constant,
    plus the rationale and impact estimate. Node 3 fills the list;
    Node 4 persists each row.

All three are Pydantic so a wrapper-level output validator can assert
the entire `TrainerState` is well-formed before any HITL card or DB row
is created.
"""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class AgentPerformance(BaseModel):
    """Aggregate per-agent KPIs over the rolling 7-day window."""

    agent_name: str

    # Volume + outcomes
    runs_total: int = 0
    runs_failure: int = 0
    failure_rate: float = 0.0          # runs_failure / max(runs_total, 1)

    # HITL signal — founder overruling the agent is the strongest "this
    # prompt is wrong" feedback we have.
    hitl_rejections: int = 0
    hitl_approvals: int = 0
    rejection_ratio: float = 0.0       # rejections / max(approvals + rejections, 1)

    # Cost + latency signal
    total_cost_usd: float = 0.0
    p95_duration_ms: float = 0.0
    median_cost_usd: float = 0.0
    p95_cost_usd: float = 0.0
    cost_variance_ratio: float = 0.0   # p95_cost / max(median_cost, eps)

    # R4-style regressions already flagged by the deterministic alert.
    regression_flags: int = 0


class UnderperformingAgent(BaseModel):
    """An agent that crossed at least one Node-2 threshold."""

    agent_name: str

    # Human-readable summary the LLM consumes in Node 3. Includes the
    # specific thresholds breached. Deliberately not free-form — built
    # from the metrics row.
    failure_signal: str = Field(min_length=20)

    # Snapshot of current prompts via AST parsing of prompts.py. Maps
    # constant_name → literal_text. Never imports/execs the module.
    current_prompts: dict[str, str] = Field(default_factory=dict)

    metrics: AgentPerformance


class PromptProposal(BaseModel):
    """One LLM-generated rewrite for a single prompt constant.

    The wrapper's post-AI stage validates this whole object. The
    `rationale` field enforces the TWAT-spec guardrail #2: every
    proposal must explain why this improves performance.
    """

    target_agent_name: str
    prompt_constant_name: str             # e.g. "NURTURE_SYSTEM"

    # Snapshot of the prompt at proposal time. Stored on the DB row so a
    # human applying the change later can detect drift via sha256.
    current_text: str
    current_text_sha256: str = Field(min_length=64, max_length=64)

    # The LLM's rewrite.
    proposed_text: str = Field(min_length=20)

    # GUARDRAIL: rationale length matches the DB CHECK constraint.
    # A model that emits "Better prompt." is auto-rejected at validation.
    rationale: str = Field(min_length=50, max_length=4000)

    expected_impact: dict = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class TrainerState(AgentRunState):
    """LangGraph state threaded through TRAINER's 4 nodes."""

    agent_name: str = "trainer"
    run_date: date = Field(default_factory=date.today)
    window_days: int = 7
    iso_week: str = ""                      # 'YYYY-Www' filled by Node 1

    # Node 1 → Node 2
    performance_summaries: list[AgentPerformance] = Field(default_factory=list)

    # Node 2 → Node 3
    underperformers: list[UnderperformingAgent] = Field(default_factory=list)

    # Node 3 → Node 4
    proposals: list[PromptProposal] = Field(default_factory=list)

    # Node 4 outputs
    review_ids: list[UUID] = Field(default_factory=list)
    proposals_persisted: int = 0
    proposals_approved: int = 0
    proposals_rejected: int = 0

    # Lifecycle / routing
    decision: Literal["pending", "approved", "rejected", "mixed"] = "pending"
    no_signal: bool = False                 # set True by Node 1 to short-circuit
