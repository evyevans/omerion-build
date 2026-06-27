from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import MarketAccount


class MarketMapperInput(AgentInput):
    skill: Literal["market-mapper"] = "market-mapper"


class MarketMapperOutput(AgentOutput):
    candidates: list[MarketAccount] = Field(default_factory=list)
    accounts_upserted: int = 0
    accounts_skipped_threshold: int = 0


CONTRACT = AgentContract(
    skill="market-mapper",
    input_model=MarketMapperInput,
    output_model=MarketMapperOutput,
    min_confidence=0.60,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=1800,
)

register_contract(CONTRACT)
