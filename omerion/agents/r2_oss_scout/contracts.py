from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import ScoredCandidate


class OssScoutInput(AgentInput):
    skill: Literal["r2-oss-scout"] = "r2-oss-scout"


class OssScoutOutput(AgentOutput):
    scored: list[ScoredCandidate] = Field(default_factory=list)
    inserted: int = 0
    duplicates: int = 0


CONTRACT = AgentContract(
    skill="r2-oss-scout",
    input_model=OssScoutInput,
    output_model=OssScoutOutput,
    min_confidence=0.60,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=1800,
)

register_contract(CONTRACT)
