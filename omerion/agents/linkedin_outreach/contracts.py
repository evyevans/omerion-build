"""LinkedIn Outreach — wrapper contract (Wave 1.9).

This is the **canonical migration template** for routing an agent through
`omerion_core.runtime.agent_wrapper`. Other 14 agents follow this shape:

  1. Define `<AgentName>Input` (extends `AgentInput`) — caller's required fields.
  2. Define `<AgentName>Output` (extends `AgentOutput`) — output fields the
     wrapper validates and any fields the agent emits downstream.
  3. Build the `AgentContract` with `min_confidence`, optional `value_extractor`
     for value-bound enforcement, and mutex TTL.
  4. Call `register_contract(CONTRACT)` at module import time.

The agent's `graph.py` continues to run unchanged — the wrapper's post-AI
stage parses the final state into `LinkedInOutreachOutput`, runs
`style_guard.filter()` over the drafts, and verifies every recipient
contact_id is in the wrapper-filtered cohort.

The hard recipient guarantee is the single most important invariant for
this agent: an LLM cannot construct a contact_id that wasn't in the
opted-in cohort. If a draft addresses a contact not in the cohort, the
wrapper raises `RecipientNotInCohort` and the run fails — no LinkedIn
DM is ever sent to a hallucinated identity.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)


class LinkedInOutreachInput(AgentInput):
    """Input the wrapper hands to the LinkedIn agent.

    `cohort` comes from icp-scoring's CONTACT_SCORED fan-out. The wrapper
    filters opted-out contacts out of this list *before* the agent sees it;
    the agent then plans/drafts/sends touches only against the filtered set.
    """

    skill: Literal["linkedin-outreach"] = "linkedin-outreach"
    track: Literal["cold", "warm"] = "cold"
    daily_cap: int = Field(default=30, ge=1, le=100)


class LinkedInOutreachOutput(AgentOutput):
    """Output the wrapper validates after the graph completes.

    `human_facing_drafts` is the list of DM bodies — the wrapper runs
    `style_guard.filter()` over each entry and raises `StyleViolation` if
    any draft trips the banned-phrase or filler-adverb checks.

    `recipients` is the list of contact_ids actually sent to — the wrapper
    asserts every one is in the input cohort.
    """

    sent_count: int = 0
    skipped_capped: int = 0
    skipped_stopped: int = 0
    rag_signals_written: int = 0


# Confidence threshold: LinkedIn DMs are reversible only by reputation
# damage, so we want a relatively strict floor. Below 0.65 the wrapper
# routes the run to HITL instead of sending.
CONTRACT = AgentContract(
    skill="linkedin-outreach",
    input_model=LinkedInOutreachInput,
    output_model=LinkedInOutreachOutput,
    min_confidence=0.65,
    # No value_extractor — LinkedIn outreach does not write dollar amounts.
    # Value-bound enforcement is owned by offer-matching (Wave 2.2).
    mutex_ttl_seconds=1800,
)


def _register() -> None:
    """Side-effect registration on import. Called by agents/__init__.py."""
    register_contract(CONTRACT)


_register()
