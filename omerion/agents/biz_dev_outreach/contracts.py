from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import ApplicationDraft


class BizDevOutreachInput(AgentInput):
    skill: Literal["biz-dev-outreach"] = "biz-dev-outreach"


class BizDevOutreachOutput(AgentOutput):
    drafts: list[ApplicationDraft] = Field(default_factory=list)
    submitted_count: int = 0
    skipped_duplicate: int = 0
    skipped_low_relevance: int = 0
    skipped_low_rank: int = 0
    skipped_scam: int = 0
    drafts_with_flags: int = 0


CONTRACT = AgentContract(
    skill="biz-dev-outreach",
    input_model=BizDevOutreachInput,
    output_model=BizDevOutreachOutput,
    min_confidence=0.65,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=1800,
)

register_contract(CONTRACT)
