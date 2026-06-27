"""State for Lead Scraper & Enricher (Agent #3)."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

Persona = Literal[
    "ops_leader",
    "revenue_leader",
    "sme_founder",
    "agency_owner",
    "ecommerce_operator",
    "professional_services_owner",
    "saas_founder",
    "hr_talent_leader",
    "finance_ops",
    "unknown",
]


class EnrichedContact(BaseModel):
    """Normalized contact ready for upsert into Supabase."""
    contact_id: UUID | None = None
    account_id: UUID
    full_name: str
    email: str | None = None
    linkedin_url: str | None = None
    title: str | None = None
    persona: Persona = "unknown"
    locale: str | None = None
    source: str
    source_url: str
    email_confidence: float = 0.0


class EnricherState(AgentRunState):
    """LangGraph state for one batch-enrichment run."""
    agent_name: str = "lead_scraper_enricher"
    run_date: date = Field(default_factory=date.today)

    # Set by the Discord inbound route when the run originates from a channel
    # message rather than an event payload. parse_discord_intent_node reads this
    # and creates placeholder accounts so the rest of the graph can proceed
    # with account_ids populated.
    discord_message: str | None = None

    account_ids: list[UUID] = Field(default_factory=list)
    enriched: list[EnrichedContact] = Field(default_factory=list)
    enrichment_cost_usd: float = 0.0        # accumulated autonomous-loop cost across accounts
    batch_approved: bool = False            # set by the G2 hitl_gate; upsert is a no-op unless True
    duplicates_skipped: int = 0
    upserted: int = 0
    emitted_events: int = 0
