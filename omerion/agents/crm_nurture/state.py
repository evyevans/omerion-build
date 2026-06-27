"""State for CRM Warm Leads Nurture (Agent #5)."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

Channel = Literal["email"]
LeadStage = Literal[
    "new_lead", "contacted", "engaged",
    "proposal_sent", "meeting_booked", "won", "lost",
]


class NurtureCandidate(BaseModel):
    contact_id: UUID
    account_id: UUID | None = None
    persona: str = "unknown"
    stage: LeadStage = "new_lead"
    first_name: str = ""
    email: str | None = None
    phone: str | None = None
    pain_signal: str = ""
    market: str = ""
    last_touch_at: str | None = None
    last_touch_reference: str = ""             # one-line context for personalization
    days_since_last_touch: int = 999
    engagement_score: float = 0.0              # opens+clicks weighted, last 24h
    rag_context: str = ""                      # injected by rag_augment node; passed to draft prompts
    custom_instructions: str = ""              # per-contact ad-hoc instructions parsed from a Discord prompt


class NurtureDraft(BaseModel):
    draft_id: UUID = Field(default_factory=uuid4)
    contact_id: UUID
    channel: Channel
    template_key: str
    subject: str = ""                          # email only
    body: str
    persona: str
    approved: bool = False
    sent_provider_id: str | None = None        # gmail message id
    gmail_draft_id: str | None = None          # draft id if synced to Gmail


class NurtureState(AgentRunState):
    agent_name: str = "crm_nurture"
    run_date: date = Field(default_factory=date.today)

    # Realtime triggers can pre-populate this; cron leaves it empty.
    candidate_contact_ids: list[UUID] = Field(default_factory=list)

    # Raw user prompt from a Discord-triggered run (e.g. "draft an email for
    # John Doe saying X"). Parsed by the intent node into a target contact +
    # custom_instructions before the candidate cohort is built.
    discord_message: str | None = None

    candidates: list[NurtureCandidate] = Field(default_factory=list)
    drafts: list[NurtureDraft] = Field(default_factory=list)
    review_id: str | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"
    sent_count: int = 0
    failed_count: int = 0
    skipped_stop_condition: int = 0
    skipped_cooldown: int = 0
    rag_signals_written: int = 0              # count of Pinecone signal vectors written
