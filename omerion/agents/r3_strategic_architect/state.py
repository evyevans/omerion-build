"""State for R3 Strategic Architect."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

Impact = Literal["low", "medium", "high"]
Effort = Literal["S", "M", "L", "XL"]
ImpactTag = Literal["daam", "capa", "remi", "asap", "internal_os"]


class SignalBundle(BaseModel):
    """Raw evidence the architect synthesizes over."""
    rd_insights: list[dict] = Field(default_factory=list)
    oss_candidates: list[dict] = Field(default_factory=list)
    attribution_reports: list[dict] = Field(default_factory=list)


class DesignProposal(BaseModel):
    title: str
    problem_statement: str
    hypothesis: str
    design_doc_md: str
    target_module: ImpactTag
    impact: Impact
    effort: Effort
    priority_score: float = 0.0
    supporting_insight_ids: list[str] = Field(default_factory=list)
    supporting_oss_ids: list[str] = Field(default_factory=list)
    supporting_report_ids: list[str] = Field(default_factory=list)
    blueprint_handoff: dict = Field(default_factory=dict)
    proposal_id: UUID | None = None
    review_id: UUID | None = None
    decision: str | None = None


class ArchitectState(AgentRunState):
    agent_name: str = "r3_strategic_architect"
    run_date: date = Field(default_factory=date.today)
    lookback_days: int = 14
    signals: SignalBundle = Field(default_factory=SignalBundle)
    prior_block: str = "(none)"  # rendered semantic recall of R3's own prior proposals
    proposals: list[DesignProposal] = Field(default_factory=list)
    proposals_written: int = 0
    proposals_embedded: int = 0
    hitl_review_ids: list[str] = Field(default_factory=list)
