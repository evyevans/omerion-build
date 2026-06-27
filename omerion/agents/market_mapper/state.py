"""State for Market Mapper (Agent #1)."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

PersonaSeg = Literal[
    "ops_leader", "revenue_leader", "sme_founder", "agency_owner",
    "ecommerce_operator", "professional_services_owner", "saas_founder",
    "hr_talent_leader", "finance_ops", "unknown",
]


class MarketAccount(BaseModel):
    """A candidate account discovered in a market scrape."""
    name: str
    domain: str | None = None
    website: str | None = None
    linkedin_company_url: str | None = None
    market: str
    persona: PersonaSeg = "unknown"
    volume_estimate: int | None = None
    team_size: int | None = None
    tech_signals: list[str] = Field(default_factory=list)
    source_url: str
    raw_metadata: dict = Field(default_factory=dict)

    # Computed by the ranking step.
    volume_score: float = 0.0
    persona_fit_score: float = 0.0
    tech_maturity_score: float = 0.0
    final_score: float = 0.0
    qualifies: bool = False
    account_id: UUID | None = None


class MarketMapState(AgentRunState):
    agent_name: str = "market_mapper"
    run_date: date = Field(default_factory=date.today)

    target_markets: list[str] = Field(default_factory=list)
    candidates: list[MarketAccount] = Field(default_factory=list)
    accounts_upserted: int = 0
    accounts_skipped_threshold: int = 0
