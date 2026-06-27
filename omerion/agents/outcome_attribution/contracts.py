"""Outcome Attribution — wrapper contract (Wave 1.9).

The most operationally-sensitive of the remaining agents. It computes
the pre/post KPI delta for a deployment and writes an attribution
report. The numbers it produces feed Mission Control's revenue view
and any future per-client invoicing logic.

**Why it has the strictest confidence floor (0.80):** the report's
`delta_pct` and `proof_point` get cited in client-facing case studies.
A confidently-wrong attribution is worse than no attribution at all —
the wrapper routes sub-0.80 confidence to HITL so a human signs off
on the dollar story before it leaves the system.

**Why no `value_extractor`:** per plan §6 and the operating laws, the
agent itself NEVER produces a dollar amount that becomes a revenue
ledger entry. The agent's job is to *narrate* the delta, not to write
to `business_outcomes`. If/when the attribution pipeline records an
outcome row, that write goes through `business_outcomes.record_outcome()`
with `source="deterministic_compute"` — Stripe + CRM joins do the
arithmetic deterministically, and the wrapper never sees the dollars.
"""
from __future__ import annotations

from typing import Literal

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)


class OutcomeAttributionInput(AgentInput):
    skill: Literal["outcome-attribution"] = "outcome-attribution"
    deployment_id: str  # UUID string
    window_days: int = 30


class OutcomeAttributionOutput(AgentOutput):
    """The wrapper-validated output shape.

    `delta_pct_max` is surfaced as a float so the wrapper has a numeric
    handle for future bound checks (e.g., "no single deployment may
    claim >500% delta without HITL approval"). Today no value_extractor
    is wired — but the shape is here so adding one is a one-line change
    in the contract below.
    """

    deployment_id: str = ""
    report_id: str | None = None
    kpi_count: int = 0
    delta_pct_max: float = 0.0       # largest |delta_pct| across all KPIs
    revenue_post: float = 0.0
    significant_count: int = 0       # count of KPIs marked .significant
    feedback_count: int = 0


CONTRACT = AgentContract(
    skill="outcome-attribution",
    input_model=OutcomeAttributionInput,
    output_model=OutcomeAttributionOutput,
    # Highest floor of any agent — revenue narrative requires founder
    # sign-off below this threshold.
    min_confidence=0.80,
    # No value_extractor: the agent does not produce dollar amounts that
    # flow into the revenue ledger. The business_outcomes.record_outcome()
    # source-of-truth gate (Wave 2.3) blocks `source="agent_inference"`
    # so even an attempt to bypass this would fail at the DB layer.
    mutex_ttl_seconds=1800,
)


def _register() -> None:
    register_contract(CONTRACT)


_register()
