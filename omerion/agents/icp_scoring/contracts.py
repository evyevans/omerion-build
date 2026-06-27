from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import ScoredContact


class IcpScoringInput(AgentInput):
    skill: Literal["icp-scoring"] = "icp-scoring"


class IcpScoringOutput(AgentOutput):
    scored: list[ScoredContact] = Field(default_factory=list)
    shortlist: list[ScoredContact] = Field(default_factory=list)
    digest_sent: bool = False


CONTRACT = AgentContract(
    skill="icp-scoring",
    input_model=IcpScoringInput,
    output_model=IcpScoringOutput,
    min_confidence=0.70,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=600,
)

register_contract(CONTRACT)
