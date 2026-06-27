"""State for High-Quality Lead Scraping (Agent #2)."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class SourceFinding(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    discovered_via: str = ""                  # "web", "linkedin", "github", "podcast", ...


class Dossier(BaseModel):
    account_id: UUID
    contact_id: UUID | None = None
    summary: str = ""
    track_record: str = ""
    current_niche: str = ""
    pain_signals: list[str] = Field(default_factory=list)
    outreach_angle: str = ""
    conversation_hooks: list[str] = Field(default_factory=list)
    offer_match: dict = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    disqualification_flags: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    pinecone_ids: list[str] = Field(default_factory=list)
    dossier_id: UUID | None = None
    review_id: str | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"
    dedup_note: str = ""             # soft-flag surfaced to the founder (0.90–0.95 similarity)


class HQLState(AgentRunState):
    agent_name: str = "high_quality_lead_scraping"

    candidate_account_ids: list[UUID] = Field(default_factory=list)
    accounts: list[dict] = Field(default_factory=list)
    findings_by_account: dict[str, list[SourceFinding]] = Field(default_factory=dict)
    dossiers: list[Dossier] = Field(default_factory=list)
    dossiers_written: int = 0
    skipped_disqualified: int = 0
    skipped_low_quality: int = 0
    skipped_duplicate: int = 0       # hard semantic-dedup skips (≥0.96 similarity)
    research_cost_usd: float = 0.0   # accumulated autonomous-loop cost across accounts
    # Trigger fields
    mode: Literal["reactive", "proactive"] = "reactive"  # proactive = cron-seeded
    discord_message: str = ""        # raw message from Discord, if triggered that way
    search_hint: dict = Field(default_factory=dict)  # parsed intent (persona, tier, etc.)
