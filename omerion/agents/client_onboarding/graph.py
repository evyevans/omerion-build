"""LangGraph for Client Onboarding.

Flow:
    intake_agreement → provision_plan → hitl_review → hitl_wait
      → (approved) → provision_workspace → configure_personas → send_kickoff
                     → schedule_reporting → emit
      → (rejected) → emit_rejected
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .state import OnboardingState
from .tools import (
    apply_persona_overrides,
    draft_kickoff,
    draft_workspace_config,
    insert_client_row,
    parse_intake,
    provision_workspace,
    schedule_reporting,
    send_kickoff,
)

log = get_logger("omerion.agents.client_onboarding")


@traced_node("intake_agreement")
def intake_node(state: OnboardingState) -> OnboardingState:
    # state.discord_message is populated by event_ingress for #onboard runs.
    # (Was `(state.inputs or {})` — AgentRunState has no `inputs` field → AttributeError.)
    raw = state.discord_message or state.scratch.get("discord_message", "") or ""
    if not state.intake.raw_message and raw:
        router = ClaudeRouter()
        state.intake = parse_intake(router, raw)
    state.client_id = insert_client_row(state.intake)
    log.info(
        "onboarding_intake_received",
        client_id=str(state.client_id),
        client_name=state.intake.client_name,
    )
    return state


@traced_node("provision_plan")
def plan_node(state: OnboardingState) -> OnboardingState:
    router = ClaudeRouter()
    state.workspace_config = draft_workspace_config(router, state.intake)
    # Draft the client kickoff now so the founder approves the exact message (G1).
    state.kickoff_subject, state.kickoff_body = draft_kickoff(state.intake)
    return state


@traced_node("hitl_review")
def review_node(state: OnboardingState) -> OnboardingState:
    cfg = state.workspace_config
    context_md = (
        f"**Client:** {state.intake.client_name or '(unnamed)'}\n\n"
        f"**Industry / vertical:** {state.intake.industry} / {state.intake.vertical}\n\n"
        f"**Supabase schema:** `{cfg.supabase_schema or '—'}`\n\n"
        f"**Discord channel prefix:** `{cfg.discord_channel_prefix or '—'}`\n\n"
        f"**Enabled skills:** {', '.join(cfg.enabled_skills) or '(none)'}\n\n"
        f"**Persona overrides:** {len(cfg.persona_overrides)} keys\n\n"
        f"**Notes:** {cfg.notes or '(none)'}\n\n"
        "---\n\n"
        f"⚠️ **Kickoff email to the client** ({state.intake.contact_email or 'no email'})\n\n"
        f"_Subject:_ {state.kickoff_subject or '—'}\n\n"
        f"{state.kickoff_body or '—'}\n\n"
        "Approve to provision the workspace AND send this kickoff message to the client."
    )
    review = create_founder_review_task(
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        subject=f"Onboarding plan — {state.intake.client_name or 'new client'}",
        context_md=context_md,
        draft_ref={"kind": "onboarding_plan", "client_id": str(state.client_id)},
        correlation_id=state.correlation_id,
    )
    state.review_id = review["review_id"]
    return state


@traced_node("hitl_wait")
def wait_node(state: OnboardingState) -> OnboardingState:
    if not state.review_id:
        return state
    if state.decision in ("approved", "rejected"):
        return state
    result = interrupt({"review_id": str(state.review_id), "session_id": state.session_id})
    decisions = result.get("decisions", {}) if isinstance(result, dict) else {}
    state.decision = decisions.get(str(state.review_id), "rejected")
    return state


def _after_decision(state: OnboardingState) -> str:
    return "provision_workspace" if state.decision == "approved" else "emit_rejected"


@traced_node("provision_workspace")
def provision_node(state: OnboardingState) -> OnboardingState:
    if state.client_id is None:
        return state
    provision_workspace(state.client_id, state.workspace_config)
    return state


@traced_node("configure_personas")
def configure_node(state: OnboardingState) -> OnboardingState:
    if state.client_id is None:
        return state
    state.persona_overrides_applied = apply_persona_overrides(
        state.client_id, state.workspace_config.persona_overrides
    )
    return state


@traced_node("send_kickoff")
def kickoff_node(state: OnboardingState) -> OnboardingState:
    # Send the founder-approved draft (G1) — not auto-generated text.
    state.kickoff_sent = send_kickoff(state.intake, state.kickoff_subject, state.kickoff_body)
    return state


@traced_node("schedule_reporting")
def schedule_node(state: OnboardingState) -> OnboardingState:
    if state.client_id is None:
        return state
    state.reporting_scheduled = schedule_reporting(state.client_id)
    return state


@traced_node("emit")
def emit_node(state: OnboardingState) -> OnboardingState:
    if state.client_id is None:
        return state
    emit_event(
        EventType.CLIENT_ONBOARDED,
        source_agent=state.agent_name,
        payload={
            "client_id": str(state.client_id),
            "client_name": state.intake.client_name,
            "kickoff_sent": state.kickoff_sent,
            "reporting_scheduled": state.reporting_scheduled,
        },
        correlation_id=state.correlation_id,
    )
    return state


@traced_node("emit_rejected")
def emit_rejected_node(state: OnboardingState) -> OnboardingState:
    log.info("onboarding_rejected", client_id=str(state.client_id) if state.client_id else None)
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(OnboardingState)
    g.add_node("intake_agreement", intake_node)
    g.add_node("provision_plan", plan_node)
    g.add_node("hitl_review", review_node)
    g.add_node("hitl_wait", wait_node)
    g.add_node("provision_workspace", provision_node)
    g.add_node("configure_personas", configure_node)
    g.add_node("send_kickoff", kickoff_node)
    g.add_node("schedule_reporting", schedule_node)
    g.add_node("emit", emit_node)
    g.add_node("emit_rejected", emit_rejected_node)

    g.set_entry_point("intake_agreement")
    g.add_edge("intake_agreement", "provision_plan")
    g.add_edge("provision_plan", "hitl_review")
    g.add_edge("hitl_review", "hitl_wait")
    g.add_conditional_edges(
        "hitl_wait",
        _after_decision,
        {"provision_workspace": "provision_workspace", "emit_rejected": "emit_rejected"},
    )
    g.add_edge("provision_workspace", "configure_personas")
    g.add_edge("configure_personas", "send_kickoff")
    g.add_edge("send_kickoff", "schedule_reporting")
    g.add_edge("schedule_reporting", "emit")
    g.add_edge("emit", END)
    g.add_edge("emit_rejected", END)
    return g.compile(checkpointer=get_checkpointer())
