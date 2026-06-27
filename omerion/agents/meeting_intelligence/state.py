"""State for Meeting Intelligence & Consulting Proposal (Agent #8)."""
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
OperatorArchetype = Literal["high_velocity", "system_multiplier", "capital_allocator"]


class W5H(BaseModel):
    who: list[str] = Field(default_factory=list)
    what: str = ""
    where: str = ""
    when: str = ""
    how_much: str = ""          # replaces generic "why" — discovery needs budget/economic-buyer surface


class TTWA(BaseModel):
    trigger: str = ""
    tension: str = ""
    winning_action: str = ""    # maps to one service_package


class PersonaClassification(BaseModel):
    persona: str = "unknown"
    persona_tier: int = 3
    archetype: OperatorArchetype = "system_multiplier"
    confidence: float = 0.0


class PricingBand(BaseModel):
    price_usd: float = 0.0
    band: tuple[int, int] = (0, 0)
    rationale: str = ""


class ConsultingProposal(BaseModel):
    """`consulting_v1` proposal schema."""
    exec_summary: str = ""
    problem_statement_w5h: str = ""
    operator_archetype: OperatorArchetype | None = None
    recommended_service_package: ServicePackage | None = None
    demo_reference: DemoReference | None = None
    demo_plan: str = ""
    thirty_sixty_ninety: dict[str, str] = Field(default_factory=dict)
    pricing: PricingBand = Field(default_factory=PricingBand)
    success_metrics: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class BacklogItem(BaseModel):
    phase: Literal["phase_1", "phase_2", "phase_3"]
    title: str
    rationale: str
    effort_days: float = 1.0
    depends_on: list[str] = Field(default_factory=list)


class BlueprintDraft(BaseModel):
    account_id: UUID | None = None
    contact_id: UUID | None = None
    persona: str = "unknown"
    persona_tier: int = 3
    archetype: OperatorArchetype = "system_multiplier"
    w5h: W5H = Field(default_factory=W5H)
    ttwa: TTWA = Field(default_factory=TTWA)
    proposal: ConsultingProposal = Field(default_factory=ConsultingProposal)
    constraints: dict[str, str] = Field(default_factory=dict)
    backlog: list[BacklogItem] = Field(default_factory=list)
    hitl_flags: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class MeetingState(AgentRunState):
    agent_name: str = "meeting_intelligence"

    meeting_id: str
    transcript_text: str = ""
    transcript_sentences: list[dict] = Field(default_factory=list)
    summary_raw: str = ""
    blueprint: BlueprintDraft = Field(default_factory=BlueprintDraft)
    blueprint_id: UUID | None = None
    review_id: UUID | None = None
    hitl_review_id: UUID | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"
    hitl_regen_attempts: int = 0
    past_context_snippets: list[str] = Field(default_factory=list)
