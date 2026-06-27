"""HEALER — wrapper contract (no value extractor; HEALER doesn't produce dollar amounts)."""
from __future__ import annotations

from typing import Literal

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)


class HealerInput(AgentInput):
    skill: Literal["healer"] = "healer"
    failing_agent: str
    severity: Literal["low", "medium", "high", "critical"]
    metric: str
    metric_value: float
    alert_run_id: str | None = None


class HealerOutput(AgentOutput):
    fix_applied: bool = False
    remediation_type: str | None = None
    healing_notes: str = ""


CONTRACT = AgentContract(
    skill="healer",
    input_model=HealerInput,
    output_model=HealerOutput,
    min_confidence=0.60,
    mutex_ttl_seconds=300,   # short — one active heal per failing_agent at a time
)


def _register() -> None:
    register_contract(CONTRACT)


_register()
