"""State for Offer Matching & Playbook (Agent #7).

An offer is one of four consulting `service_package`s, paired with a
`demo_reference` (the live DAAM/CAPA/ASAP/REMI system we walk through
on the discovery call).
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

ServicePackage = Literal[
    "revenue_acceleration_engine",
    "ops_intelligence_layer",
    "research_decision_stack",
    "process_automation_suite",
]
DemoReference = Literal["DAAM", "CAPA", "ASAP", "REMI"]


class PlaybookPhase(BaseModel):
    label: Literal["30", "60", "90"]
    objective: str
    deliverables: list[str] = Field(default_factory=list)
    success_metrics: list[str] = Field(default_factory=list)


ValueBucket = Literal["S", "M", "L", "XL"]


class OfferProposal(BaseModel):
    contact_id: UUID
    account_id: UUID | None = None
    persona: str = "unknown"
    persona_tier: int = 1
    service_package: ServicePackage | None = None
    demo_reference: DemoReference | None = None
    price_band: dict = Field(default_factory=dict)   # {min, max, currency}
    # Wave 2.2: `value_est_usd` is the persisted dollar figure. It comes from
    # the deterministic mapping of value_bucket → settings.value_bucket_ranges_usd
    # (midpoint), or from the offer_packages.price_band midpoint as a fallback.
    # The LLM never produces a raw dollar number — it picks the service_package;
    # code derives the bucket; code derives the dollar. The wrapper enforces the
    # MAX_OPPORTUNITY_VALUE_USD cap above which HITL is required.
    value_bucket: ValueBucket | None = None
    value_est_usd: float = 0.0
    rationale: str = ""
    playbook: list[PlaybookPhase] = Field(default_factory=list)
    memo_md: str = ""
    confidence: float = 0.0
    similar_account_ids: list[str] = Field(default_factory=list)


class OfferMatchingState(AgentRunState):
    agent_name: str = "offer_matching"

    candidate_contact_ids: list[UUID] = Field(default_factory=list)
    hot_contacts: list[dict] = Field(default_factory=list)
    proposals: list[OfferProposal] = Field(default_factory=list)
    review_id: UUID | None = None
    hitl_review_id: UUID | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"
    opportunities_created: int = 0
    # Keyed by contact_id → list of Pinecone match IDs from find_similar_wins().
    # Populated in propose_node BEFORE the HITL interrupt so replay uses the
    # same results the founder approved (Pinecone is non-deterministic per call).
    pinecone_cache: dict[str, list[str]] = Field(default_factory=dict)
