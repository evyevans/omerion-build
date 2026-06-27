from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import DesignProposal


class StrategicArchitectInput(AgentInput):
    skill: Literal["r3-strategic-architect"] = "r3-strategic-architect"


class StrategicArchitectOutput(AgentOutput):
    proposals: list[DesignProposal] = Field(default_factory=list)
    proposals_written: int = 0


CONTRACT = AgentContract(
    skill="r3-strategic-architect",
    input_model=StrategicArchitectInput,
    output_model=StrategicArchitectOutput,
    min_confidence=0.65,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=1800,
)

register_contract(CONTRACT)
