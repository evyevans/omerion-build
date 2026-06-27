from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import Dossier, SourceFinding


class HighQualityLeadScrapingInput(AgentInput):
    skill: Literal["hq-lead-scraping"] = "hq-lead-scraping"


class HighQualityLeadScrapingOutput(AgentOutput):
    findings_by_account: dict[str, list[SourceFinding]] = Field(default_factory=dict)
    dossiers: list[Dossier] = Field(default_factory=list)
    dossiers_written: int = 0
    skipped_disqualified: int = 0
    skipped_low_quality: int = 0


CONTRACT = AgentContract(
    skill="hq-lead-scraping",
    input_model=HighQualityLeadScrapingInput,
    output_model=HighQualityLeadScrapingOutput,
    min_confidence=0.70,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=1800,
)

register_contract(CONTRACT)
