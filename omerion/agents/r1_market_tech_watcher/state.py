"""State for R1 Market/Tech Watcher."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

ImpactTag = Literal["daam", "capa", "remi", "asap", "internal_os"]
SourceType = Literal["rss", "github_release", "newsletter", "blog"]


class RawSignal(BaseModel):
    source_url: str
    source_type: SourceType
    title: str
    raw_content: str = ""
    published_at: str | None = None


class TaggedInsight(BaseModel):
    source_url: str
    source_type: SourceType
    title: str
    summary: str
    impact_tag: ImpactTag
    estimated_priority: Literal["high", "medium", "low"] = "low"
    raw_content: str = ""
    metadata: dict = Field(default_factory=dict)
    insight_id: UUID | None = None


class WatcherState(AgentRunState):
    agent_name: str = "r1_market_tech_watcher"
    run_date: date = Field(default_factory=date.today)
    raw: list[RawSignal] = Field(default_factory=list)
    insights: list[TaggedInsight] = Field(default_factory=list)
    inserted: int = 0
    duplicates: int = 0                  # URL-based duplicates (same link)
    semantic_duplicates: int = 0         # ≥0.96 cosine hard-skips (same story, different URL)
