from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import WorkspaceConfig


class ClientOnboardingInput(AgentInput):
    skill: Literal["client-onboarding"] = "client-onboarding"


class ClientOnboardingOutput(AgentOutput):
    workspace_config: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    persona_overrides_applied: int = 0
    kickoff_sent: bool = False
    reporting_scheduled: bool = False


CONTRACT = AgentContract(
    skill="client-onboarding",
    input_model=ClientOnboardingInput,
    output_model=ClientOnboardingOutput,
    min_confidence=0.70,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=1800,
)

register_contract(CONTRACT)
