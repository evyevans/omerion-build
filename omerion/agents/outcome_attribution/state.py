"""State for Outcome Attribution & Feedback (Agent #10)."""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class KpiDelta(BaseModel):
    name: str
    pre_mean: float = 0.0
    post_mean: float = 0.0
    delta_abs: float = 0.0
    delta_pct: float = 0.0          # (post - pre) / max(|pre|, eps)
    sample_pre: int = 0
    sample_post: int = 0
    significant: bool = False       # |delta_pct| >= min_delta_threshold


class FeedbackItem(BaseModel):
    target: str                      # e.g. "icp_scoring_weights" | "offer_templates" | "rd_backlog"
    recommendation: str
    rationale: str
    confidence: float = 0.5


class AttributionState(AgentRunState):
    agent_name: str = "outcome_attribution"

    deployment_id: UUID
    client_id: UUID | None = None
    persona: str | None = None               # drives kpi_definitions lookup
    go_live_at: str = ""                     # ISO8601 — anchor for pre/post split
    window_days: int = 30

    kpi_deltas: list[KpiDelta] = Field(default_factory=list)
    revenue_pre: float = 0.0
    revenue_post: float = 0.0
    conversion_rate_pre: float = 0.0
    conversion_rate_post: float = 0.0

    summary_md: str = ""
    proof_point: str = ""
    feedback: list[FeedbackItem] = Field(default_factory=list)
    report_id: UUID | None = None
