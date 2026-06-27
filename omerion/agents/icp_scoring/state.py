"""State for ICP Fit & 'Why Now' Scoring (Agent #6)."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class SubScores(BaseModel):
    fit: float = 0.0
    intent: float = 0.0
    timing: float = 0.0


class ScoredContact(BaseModel):
    contact_id: UUID
    account_id: UUID
    persona: str
    fit: float = 0.0
    intent: float = 0.0
    timing: float = 0.0
    final: float = 0.0
    segment: str = "cold"           # hot | warm | watchlist | cold
    explanations: dict[str, str] = Field(default_factory=dict)


class ScoringState(AgentRunState):
    agent_name: str = "icp_scoring"
    run_date: date = Field(default_factory=date.today)
    candidate_contact_ids: list[UUID] = Field(default_factory=list)
    contacts: list[dict] = Field(default_factory=list)       # raw rows from supabase
    scored: list[ScoredContact] = Field(default_factory=list)
    shortlist: list[ScoredContact] = Field(default_factory=list)
    digest_sent: bool = False
