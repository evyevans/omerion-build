"""TRAINER — wrapper contract (Wave 5).

Strictest confidence floor of any agent except `outcome_attribution`
(0.80). The reasoning: a prompt rewrite affects every future run of the
target agent. Sub-0.75 confidence means TRAINER itself isn't sure
about its diagnosis — that uncertainty deserves a wrapper-level HITL
before the proposals even reach the graph's per-proposal HITL.

Two-layer HITL:
  1. Wrapper-level: if TrainerOutput.confidence < 0.75, wrapper routes
     the WHOLE run to HITL with status='hitl_waiting'.
  2. Graph-level (Node 4): each individual proposal gets a separate
     HITL review card so the founder can approve some and reject others.

No `value_extractor` — TRAINER never produces dollar amounts and is
ineligible to write to `business_outcomes` regardless of source (the
source-of-truth gate in Wave 2.3 already enforces this).

`mutex_ttl_seconds=3600` because meta-evaluating 6 agents × 2 prompts
each = up to 12 HEAVY-tier Claude calls. The longer mutex covers the
full sweep so a misfiring scheduler can't start a second TRAINER mid-run.
"""
from __future__ import annotations

from typing import Literal

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)


class TrainerInput(AgentInput):
    skill: Literal["trainer"] = "trainer"
    window_days: int = 7


class TrainerOutput(AgentOutput):
    proposals_count: int = 0
    proposals_persisted: int = 0
    proposals_approved: int = 0
    proposals_rejected: int = 0
    underperformers_count: int = 0
    no_signal: bool = False


CONTRACT = AgentContract(
    skill="trainer",
    input_model=TrainerInput,
    output_model=TrainerOutput,
    # Strictest floor of any agent except outcome_attribution (0.80) —
    # prompt changes affect every future run of the target agent.
    min_confidence=0.75,
    # No value_extractor — TRAINER never produces dollar amounts.
    mutex_ttl_seconds=3600,  # 1 hour covers the multi-agent meta-eval
)


def _register() -> None:
    register_contract(CONTRACT)


_register()
