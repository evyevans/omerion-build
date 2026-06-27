from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import TaggedInsight


class MarketTechWatcherInput(AgentInput):
    skill: Literal["r1-market-tech-watcher"] = "r1-market-tech-watcher"


class MarketTechWatcherOutput(AgentOutput):
    insights: list[TaggedInsight] = Field(default_factory=list)
    inserted: int = 0
    duplicates: int = 0


CONTRACT = AgentContract(
    skill="r1-market-tech-watcher",
    input_model=MarketTechWatcherInput,
    output_model=MarketTechWatcherOutput,
    min_confidence=0.55,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=1800,
)

register_contract(CONTRACT)
