"""LangGraph for Meeting Intelligence & Blueprint (Agent #8).

Flow:
    fetch → extract_w5h → extract_ttwa → backlog → flags → persist
      → embed → hitl_wait → (approved|rejected|regen) → emit → END

HITL: founder approves/rejects via Sheets Review Queue. On rejection
with feedback we regenerate (max 3 attempts per `regen_max_attempts`).
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .state import BlueprintDraft, MeetingState
from .tools import (
    build_backlog,
    chunk_and_embed_transcript,
    classify_persona,
    extract_ttwa,
    extract_w5h,
    fetch_transcript,
    persist_blueprint,
    query_past_context,
    raise_flags,
    synthesize_proposal,
)

log = get_logger("omerion.agents.meeting_intelligence")

MAX_REGEN = 3


@traced_node("fetch_transcript")
async def fetch_node(state: MeetingState) -> MeetingState:
    payload = await fetch_transcript(state.meeting_id)
    state.transcript_text = payload["text"]
    state.transcript_sentences = payload["sentences"]
    state.summary_raw = payload["summary_raw"]
    return state


@traced_node("extract_w5h")
def w5h_node(state: MeetingState) -> MeetingState:
    router = ClaudeRouter()
    state.blueprint.w5h = extract_w5h(router, state.transcript_text)
    return state


@traced_node("extract_ttwa")
def ttwa_node(state: MeetingState) -> MeetingState:
    router = ClaudeRouter()
    state.blueprint.ttwa = extract_ttwa(router, state.blueprint.w5h, state.transcript_text)
    return state


@traced_node("classify_persona")
def persona_node(state: MeetingState) -> MeetingState:
    router = ClaudeRouter()
    pc = classify_persona(router, state.blueprint.w5h, state.transcript_text)
    state.blueprint.persona = pc.persona
    state.blueprint.persona_tier = pc.persona_tier
    state.blueprint.archetype = pc.archetype
    return state


@traced_node("query_context")
def query_context_node(state: MeetingState) -> MeetingState:
    """Retrieve similar past meeting snippets for the same account from Pinecone.

    No-op (returns []) until the transcripts namespace has meaningful data.
    Safe to call on every run — Pinecone errors are caught and logged, not raised.
    """
    snippets = query_past_context(
        account_id=str(state.blueprint.account_id) if state.blueprint.account_id else None,
        w5h_what=state.blueprint.w5h.what,
    )
    state.past_context_snippets = snippets
    log.info("meeting_intel_context_loaded", snippets=len(snippets), meeting_id=state.meeting_id)
    return state


@traced_node("synthesize_proposal")
def proposal_node(state: MeetingState) -> MeetingState:
    router = ClaudeRouter()
    fields = settings.agent("meeting_intelligence")["constraint_fields"]
    constraints = {f: state.blueprint.constraints.get(f, "") for f in fields}
    state.blueprint.proposal = synthesize_proposal(
        router,
        state.blueprint.persona,
        state.blueprint.persona_tier,
        state.blueprint.w5h,
        state.blueprint.ttwa,
        constraints,
        archetype=state.blueprint.archetype,
        past_context_snippets=state.past_context_snippets or [],
    )
    return state


@traced_node("build_backlog")
def backlog_node(state: MeetingState) -> MeetingState:
    router = ClaudeRouter()
    fields = settings.agent("meeting_intelligence")["constraint_fields"]
    constraints = {f: state.blueprint.constraints.get(f, "") for f in fields}
    state.blueprint.backlog = build_backlog(router, state.blueprint.proposal, constraints)
    return state


@traced_node("raise_flags")
def flags_node(state: MeetingState) -> MeetingState:
    router = ClaudeRouter()
    flags, confidence = raise_flags(router, state.blueprint)
    state.blueprint.hitl_flags = flags
    state.blueprint.confidence = confidence
    return state


@traced_node("embed_transcript")
def embed_transcript_node(state: MeetingState) -> MeetingState:
    """Embed the transcript ONCE, after classify_persona so persona is set.

    Positioned after query_context so the current meeting is NOT in Pinecone
    when query_context runs — prevents self-match in similar-transcript lookup.
    Still runs only once before the synthesize→regen loop (avoids 2-3× re-embed).
    Uses the fleet manifest metadata pattern (agent_id/department/namespace/run_date).
    """
    from datetime import date, datetime, timezone as _tz

    written = chunk_and_embed_transcript(
        state.meeting_id,
        state.transcript_text,
        metadata={
            # Manifest pattern — matches outreach_signals / growth_contacts standard
            "agent_id": "meeting_intelligence",
            "department": "client_delivery",
            "namespace": "transcripts",
            "run_date": date.today().isoformat(),
            "content_date": datetime.now(_tz.utc).isoformat(),
            # Domain-specific fields
            "account_id": str(state.blueprint.account_id) if state.blueprint.account_id else "",
            "contact_id": str(state.blueprint.contact_id) if state.blueprint.contact_id else "",
            "source_url": f"fireflies://{state.meeting_id}",
            "meeting_id": state.meeting_id,
            "persona": state.blueprint.persona,   # set by classify_persona
        },
    )
    log.info("transcript_embedded", chunks=written, meeting_id=state.meeting_id)
    return state


@traced_node("persist")
def persist_node(state: MeetingState) -> MeetingState:
    # Blueprint only — transcript embedding happens once in embed_transcript_node.
    state.blueprint_id = persist_blueprint(state.blueprint, state.meeting_id, state.correlation_id)
    return state


@traced_node("create_review")
def create_review_node(state: MeetingState) -> MeetingState:
    prop = state.blueprint.proposal
    subject = (
        f"Consulting proposal ready — {prop.recommended_service_package or 'draft'} "
        f"(demo: {prop.demo_reference or '—'})"
    )
    context_md = (
        f"**Persona:** {state.blueprint.persona} (tier {state.blueprint.persona_tier})\n\n"
        f"**Confidence:** {state.blueprint.confidence:.2f}\n\n"
        f"**Flags:** {', '.join(state.blueprint.hitl_flags) or 'none'}\n\n"
        f"**Problem:** {state.blueprint.w5h.what or '—'}\n\n"
        f"**Recommended package:** {prop.recommended_service_package or '—'}"
        f" @ ${int(prop.pricing.price_usd):,}\n\n"
        f"**Demo plan:** {prop.demo_plan or '—'}"
    )
    emit_event(
        EventType.BLUEPRINT_DRAFT_CREATED,
        source_agent=state.agent_name,
        payload={"blueprint_id": str(state.blueprint_id), "meeting_id": state.meeting_id},
        correlation_id=state.correlation_id,
    )
    review = create_founder_review_task(
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        subject=subject,
        context_md=context_md,
        draft_ref={"blueprint_id": str(state.blueprint_id), "meeting_id": state.meeting_id},
        correlation_id=state.correlation_id,
    )
    state.review_id = review["review_id"]
    state.hitl_review_id = review["review_id"]
    return state


@traced_node("hitl_wait")
def hitl_wait_node(state: MeetingState) -> MeetingState:
    if not state.review_id:
        return state
    # Replay guard: on checkpoint resume, decision is already set — skip re-interrupt.
    if state.decision in ("approved", "rejected"):
        return state
    result = interrupt({"review_id": str(state.review_id), "session_id": state.session_id})
    decisions = result.get("decisions", {})
    state.decision = decisions.get(str(state.review_id), "rejected")
    state.scratch["decision_notes"] = result.get("decision_notes")
    if state.decision == "rejected":
        state.hitl_regen_attempts += 1
        # Re-open backlog with founder feedback for the next synthesis pass.
        # Moved here from _after_decision so the counter is checkpointed.
        state.blueprint = BlueprintDraft(
            account_id=state.blueprint.account_id,
            contact_id=state.blueprint.contact_id,
            persona=state.blueprint.persona,
            persona_tier=state.blueprint.persona_tier,
            w5h=state.blueprint.w5h,
            ttwa=state.blueprint.ttwa,
            constraints={**state.blueprint.constraints,
                         "founder_feedback": state.scratch.get("decision_notes") or ""},
        )
    return state


def _after_decision(state: MeetingState) -> str:
    if state.decision == "approved":
        return "emit_approved"
    if state.decision == "rejected" and state.hitl_regen_attempts < MAX_REGEN:
        return "synthesize_proposal"
    return "emit_rejected"


@traced_node("emit_approved")
def emit_approved_node(state: MeetingState) -> MeetingState:
    from omerion_core.clients.supabase_client import supabase
    # Why: emit only if persistence succeeds, so downstream subscribers never see
    # an "approved" event for a blueprint that didn't actually flip in the DB.
    resp = (
        supabase.table("blueprints")
        .update({"status": "approved"})
        .eq("blueprint_id", str(state.blueprint_id))
        .in_("status", ["draft", "pending"])
        .execute()
    )
    if not resp.data:
        # Either already approved (idempotent checkpoint replay) or row missing.
        existing = (
            supabase.table("blueprints")
            .select("status")
            .eq("blueprint_id", str(state.blueprint_id))
            .limit(1)
            .execute()
        )
        if existing.data and existing.data[0]["status"] in ("approved", "rejected"):
            pass  # idempotent replay — already terminal, continue
        else:
            raise RuntimeError(
                f"failed to mark blueprint {state.blueprint_id} approved: no row updated"
            )
    emit_event(
        EventType.BLUEPRINT_APPROVED,
        source_agent=state.agent_name,
        payload={"blueprint_id": str(state.blueprint_id), "meeting_id": state.meeting_id},
        correlation_id=state.correlation_id,
    )
    return state


@traced_node("emit_rejected")
def emit_rejected_node(state: MeetingState) -> MeetingState:
    from omerion_core.clients.supabase_client import supabase
    resp = (
        supabase.table("blueprints")
        .update({"status": "rejected"})
        .eq("blueprint_id", str(state.blueprint_id))
        .in_("status", ["draft", "pending"])
        .execute()
    )
    if not resp.data:
        # Either already rejected (idempotent checkpoint replay) or row missing.
        existing = (
            supabase.table("blueprints")
            .select("status")
            .eq("blueprint_id", str(state.blueprint_id))
            .limit(1)
            .execute()
        )
        if existing.data and existing.data[0]["status"] in ("approved", "rejected"):
            pass  # idempotent replay — already terminal, continue
        else:
            raise RuntimeError(
                f"failed to mark blueprint {state.blueprint_id} rejected: no row updated"
            )
    emit_event(
        EventType.BLUEPRINT_REJECTED,
        source_agent=state.agent_name,
        payload={
            "blueprint_id": str(state.blueprint_id),
            "regen_attempts": state.hitl_regen_attempts,
            "notes": state.scratch.get("decision_notes"),
        },
        correlation_id=state.correlation_id,
    )
    return state


def build():
    g = StateGraph(MeetingState)
    g.add_node("fetch", fetch_node)
    g.add_node("embed_transcript", embed_transcript_node)
    g.add_node("extract_w5h", w5h_node)
    g.add_node("extract_ttwa", ttwa_node)
    g.add_node("classify_persona", persona_node)
    g.add_node("query_context", query_context_node)
    g.add_node("synthesize_proposal", proposal_node)
    g.add_node("build_backlog", backlog_node)
    g.add_node("raise_flags", flags_node)
    g.add_node("persist", persist_node)
    g.add_node("create_review", create_review_node)
    g.add_node("hitl_wait", hitl_wait_node)
    g.add_node("emit_approved", emit_approved_node)
    g.add_node("emit_rejected", emit_rejected_node)

    g.set_entry_point("fetch")
    g.add_edge("fetch", "extract_w5h")
    g.add_edge("extract_w5h", "extract_ttwa")
    g.add_edge("extract_ttwa", "classify_persona")
    g.add_edge("classify_persona", "query_context")
    # embed_transcript runs AFTER query_context so: (1) persona is set in metadata,
    # (2) the current meeting isn't in Pinecone when we query for past context.
    g.add_edge("query_context", "embed_transcript")
    g.add_edge("embed_transcript", "synthesize_proposal")
    g.add_edge("synthesize_proposal", "build_backlog")
    g.add_edge("build_backlog", "raise_flags")
    g.add_edge("raise_flags", "persist")
    g.add_edge("persist", "create_review")
    g.add_edge("create_review", "hitl_wait")
    g.add_conditional_edges(
        "hitl_wait",
        _after_decision,
        {
            "emit_approved": "emit_approved",
            "emit_rejected": "emit_rejected",
            "synthesize_proposal": "synthesize_proposal",
        },
    )
    g.add_edge("emit_approved", END)
    g.add_edge("emit_rejected", END)
    from omerion_core.runtime.checkpointer import get_checkpointer
    return g.compile(checkpointer=get_checkpointer())
