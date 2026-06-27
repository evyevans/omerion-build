from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from .state import TaskSpec, ClientDeliverable


class BuildOrchestratorInput(AgentInput):
    skill: Literal["build-orchestrator"] = "build-orchestrator"


class BuildOrchestratorOutput(AgentOutput):
    tasks: list[TaskSpec] = Field(default_factory=list)
    deployment_id: UUID | None = None
    deployment_hitl_review_id: UUID | None = None
    deployment_approved: bool = False
    deployment_status: Literal["pending", "queued", "live", "failed"] = "pending"
    rollback_url: str = ""
    deliverables: list[ClientDeliverable] = Field(default_factory=list)


CONTRACT = AgentContract(
    skill="build-orchestrator",
    input_model=BuildOrchestratorInput,
    output_model=BuildOrchestratorOutput,
    min_confidence=0.75,
    value_extractor=None,
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=3600,
)

register_contract(CONTRACT)
