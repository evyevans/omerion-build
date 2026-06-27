"""Offer Matching — wrapper contract (Wave 1.9 + 2.1 + 2.2).

This is the **only** agent that owns a value_extractor. It produces
opportunity rows with dollar amounts, so the wrapper must enforce the
MAX_OPPORTUNITY_VALUE_USD cap.

Defense-in-depth:
  * The LLM never picks a dollar number — it picks `service_package`.
  * Code maps service_package → price_band (from agents.yaml) → midpoint.
  * Code derives `value_bucket` from that midpoint via the bucket ranges
    in `settings.value_bucket_ranges_usd`.
  * The wrapper extracts the *highest* `value_est_usd` across all proposals
    in this run and, if it exceeds `settings.max_opportunity_value_usd`,
    raises `ValueBoundExceeded` → run is routed to `hitl_waiting`.
  * `opportunities.idempotency_key` (Wave 1.3 migration) prevents the
    same contact/package/day from inserting twice.

A founder approves an over-cap opportunity via the existing HITL flow.
The approval re-enters the run via PostgresSaver checkpoint resume and
the persist node writes the row with the wrapper's approval log entry.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)
from omerion_core.settings import settings


class OfferMatchingInput(AgentInput):
    skill: Literal["offer-matching"] = "offer-matching"
    segment: Literal["hot", "warm", "cold"] = "hot"


class OfferProposalSummary(AgentOutput):
    """A *summary view* of OfferProposal that the wrapper can validate
    without coupling to the agent's internal Pydantic model. The wrapper
    only needs the fields that drive its checks: cohort verification
    (contact_id), confidence threshold, and value-bound enforcement.
    """

    proposals_value_est_usd: list[float] = Field(default_factory=list)
    opportunities_created: int = 0
    # `recipients` is inherited from AgentOutput; populate from
    # proposals.contact_id list so the wrapper's cohort check sees them.


def _extract_max_value(output: AgentOutput) -> float | None:
    """Pull the highest proposal value from the agent's output.

    Returns None when the output doesn't carry the field (e.g., an early
    failure or an unmigrated handler). The wrapper interprets None as
    "no value-bound check applies."

    We compare against the *maximum* across proposals because a single
    above-cap proposal should be enough to require HITL even if the
    other proposals are small.
    """
    if not hasattr(output, "proposals_value_est_usd"):
        return None
    values = getattr(output, "proposals_value_est_usd") or []
    if not values:
        return None
    return max(float(v) for v in values)


CONTRACT = AgentContract(
    skill="offer-matching",
    input_model=OfferMatchingInput,
    output_model=OfferProposalSummary,
    # Stricter than outreach: a wrong offer commits us to a price, a
    # service scope, and a 30/60/90 plan. Below this confidence → HITL.
    min_confidence=0.70,
    requires_human_approval_above_value_usd=settings.max_opportunity_value_usd,
    value_extractor=_extract_max_value,
    mutex_ttl_seconds=1800,
)


def _register() -> None:
    register_contract(CONTRACT)


_register()
