"""State for LinkedIn Cold & Warm Outreach (Agent #4)."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

Track = Literal["cold", "warm"]
StepType = Literal["connection_request", "dm"]


class PlannedStep(BaseModel):
    step_id: UUID = Field(default_factory=uuid4)
    contact_id: UUID
    track: Track
    template_key: str
    step_type: StepType
    sequence_step: int                        # 0-based offset into the sequence
    cooldown_days: int = 0                    # days since last LinkedIn touch
    persona: str = "unknown"
    persona_tier: int = 3
    persona_variant: str = "unknown"          # persona-specific angle key
    first_name: str = ""
    company: str = ""
    pain_signal: str = ""
    market: str = ""
    outreach_hook: str = ""
    rag_context: str = ""                     # injected by rag_augment node; passed to draft prompts


class DraftedMessage(BaseModel):
    step_id: UUID
    contact_id: UUID
    template_key: str
    track: Track
    step_type: StepType
    body: str
    char_count: int = 0
    approved: bool = False                    # set after HITL


class LinkedInOutreachState(AgentRunState):
    agent_name: str = "linkedin_outreach"
    run_date: date = Field(default_factory=date.today)

    # Hand-off from #6 ICP Scoring or manual override.
    candidate_contact_ids: list[UUID] = Field(default_factory=list)

    cohort: list[dict] = Field(default_factory=list)             # raw contact rows
    planned: list[PlannedStep] = Field(default_factory=list)
    drafts: list[DraftedMessage] = Field(default_factory=list)
    review_id: UUID | None = None
    hitl_review_id: UUID | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"

    sent_count: int = 0
    skipped_capped: int = 0                   # blocked by daily cap
    skipped_stopped: int = 0                  # blocked by stop_conditions
    rag_signals_written: int = 0              # count of Pinecone signal vectors written
