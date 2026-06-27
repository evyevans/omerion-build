"""State for R2 OSS Scout."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

IntegrationType = Literal["component", "pattern", "full_module", "reference_only"]


class RepoCandidate(BaseModel):
    repo_url: str
    name: str
    description: str = ""
    stars: int = 0
    language: str | None = None
    license: str | None = None
    last_commit: str | None = None
    readme_excerpt: str = ""
    search_tag: str = ""


class RubricScore(BaseModel):
    fit: float = 0.0            # 0-1 alignment to Omerion modules
    maturity: float = 0.0       # stars, recency, commits
    composability: float = 0.0  # license, modularity
    risk: float = 0.0           # license/security concerns (higher = riskier)
    overall: float = 0.0


class ScoredCandidate(BaseModel):
    repo: RepoCandidate
    rubric: RubricScore
    integration_type: IntegrationType
    recommendation: str = ""
    impact_tag: Literal["daam", "capa", "remi", "asap", "internal_os"] = "internal_os"
    candidate_id: UUID | None = None
    scored_by: Literal["haiku", "sonnet"] = "haiku"


class OssScoutState(AgentRunState):
    agent_name: str = "r2_oss_scout"
    run_date: date = Field(default_factory=date.today)
    # ── Triggering R1 insight (populated by event_ingress for rd.insight.created;
    #    empty on cron runs) ──
    insight_title: str = ""
    insight_impact_tag: str = ""
    seed_terms: list[str] = Field(default_factory=list)   # derived from the insight (empty on cron)
    raw: list[RepoCandidate] = Field(default_factory=list)
    scored: list[ScoredCandidate] = Field(default_factory=list)
    inserted: int = 0
    duplicates: int = 0
