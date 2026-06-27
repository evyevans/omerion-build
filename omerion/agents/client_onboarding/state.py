"""State for Client Onboarding."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class IntakeData(BaseModel):
    """Parsed from the Discord trigger or webhook payload."""
    client_name: str = ""
    contact_email: str = ""
    industry: str = ""
    vertical: str = ""
    agreement_url: str | None = None
    raw_message: str = ""


class WorkspaceConfig(BaseModel):
    """The plan that will be applied after HITL approval."""
    supabase_schema: str = ""
    discord_channel_prefix: str = ""
    enabled_skills: list[str] = Field(default_factory=list)
    persona_overrides: dict[str, str] = Field(default_factory=dict)
    notes: str = ""


class OnboardingState(AgentRunState):
    agent_name: str = "client_onboarding"
    run_date: date = Field(default_factory=date.today)

    # ─── Inputs ────────────────────────────────────────────────────
    discord_message: str = ""               # populated by event_ingress for #onboard runs
    intake: IntakeData = Field(default_factory=IntakeData)
    client_id: UUID | None = None

    # ─── Planned provisioning ──────────────────────────────────────
    workspace_config: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    # Kickoff message drafted before the gate so the founder approves the exact
    # client-facing text (G1) — send_kickoff sends this pre-approved draft.
    kickoff_subject: str = ""
    kickoff_body: str = ""
    review_id: UUID | None = None
    decision: Literal["pending", "approved", "rejected"] = "pending"

    # ─── Side-effect counters ──────────────────────────────────────
    persona_overrides_applied: int = 0
    kickoff_sent: bool = False
    reporting_scheduled: bool = False
