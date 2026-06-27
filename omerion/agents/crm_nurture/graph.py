"""LangGraph for CRM Warm Leads Nurture (Agent #5).

Flow:
    load_candidates
      → filter_due           (cooldown gating + engagement-driven escalation)
      → draft                (Claude — email or SMS per candidate)
      → hitl_review          (founder approves the batch via Sheets)
      → hitl_wait
      → send_or_discard      (Gmail / Twilio actual delivery on approval)
      → emit
"""
from __future__ import annotations

import uuid

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.exceptions import UserFacingError
from omerion_core.hitl.policy import Gate, ReviewItem, gate
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.outreach.signals import (
    query_outreach_signals,
    upsert_outreach_thread,
    write_outreach_signal,
)
from omerion_core.outreach.email_signals import write_email_signal
from omerion_core.telemetry.middleware import traced_node

from .prompts import REVIEW_HEADER
from .state import NurtureState
from .tools import (
    acquire_advisory_lock,
    deliver,
    draft_for,
    find_contact_id_by_name,
    load_candidates,
    log_outbound,
    needs_touch,
    parse_nurture_intent,
)

log = get_logger("omerion.agents.crm_nurture")


@traced_node("parse_discord_intent")
def parse_discord_intent_node(state: NurtureState) -> NurtureState:
    if not state.discord_message:
        return state

    router = ClaudeRouter()
    intent, cost = parse_nurture_intent(router, state.discord_message)
    contact_name = intent.get("contact_name", "")
    contact_email = intent.get("contact_email", "")
    custom_instructions = intent.get("custom_instructions", "")

    state.tokens_input += cost.get("tokens_input", 0)
    state.tokens_output += cost.get("tokens_output", 0)
    state.cost_usd += cost.get("cost_usd", 0.0)

    if contact_name:
        contact_id = find_contact_id_by_name(contact_name, email=contact_email)
        if not contact_id:
            raise UserFacingError(f"I couldn't find a contact named {contact_name} in the database.")
        state.candidate_contact_ids = [uuid.UUID(contact_id)]
        state.scratch["custom_instructions"] = custom_instructions

    return state


@traced_node("load_candidates")
def load_node(state: NurtureState) -> NurtureState:
    state.candidates = load_candidates(state.candidate_contact_ids or None)
    log.info("nurture_candidates_loaded", count=len(state.candidates))
    instructions = state.scratch.get("custom_instructions", "")
    if instructions:
        for c in state.candidates:
            c.custom_instructions = instructions
    return state


@traced_node("filter_due")
def filter_node(state: NurtureState) -> NurtureState:
    kept = []
    for c in state.candidates:
        if not needs_touch(c):
            state.skipped_cooldown += 1
            continue
        kept.append(c)
    state.candidates = kept
    return state


@traced_node("rag_augment")
async def rag_augment_node(state: NurtureState) -> NurtureState:
    """Query outreach_signals Pinecone namespace for successful past patterns.

    Injects rag_context into each NurtureCandidate so the draft node can
    reference what messaging angles worked for this persona/stage combination.
    Pinecone outage degrades gracefully: candidate proceeds with empty rag_context.
    """
    if not state.candidates:
        return state
    for candidate in state.candidates:
        try:
            candidate.rag_context = await query_outreach_signals(candidate.persona, candidate.stage)
        except Exception as exc:  # noqa: BLE001
            log.warning("nurture_rag_augment_failed", persona=candidate.persona,
                        stage=candidate.stage, error=str(exc))
            candidate.rag_context = ""
    return state


@traced_node("draft")
def draft_node(state: NurtureState) -> NurtureState:
    if not state.candidates:
        return state
    router = ClaudeRouter()
    for c in state.candidates:
        try:
            d = draft_for(router, c)
        except Exception as exc:  # noqa: BLE001
            log.error("nurture_draft_failed", contact_id=str(c.contact_id), error=str(exc))
            state.failed_count += 1
            continue
        if d is None:
            state.skipped_stop_condition += 1
            continue
        state.drafts.append(d)
    return state


@traced_node("hitl_gate")
def hitl_gate_node(state: NurtureState) -> NurtureState:
    """G1 — outbound-to-humans gate. Founder approves the batch before any send.

    Routed through the global HITL policy (one batch card). Fail-closed: anything
    not explicitly approved → 'rejected', and send_or_discard becomes a no-op.

    Replay guard: if a decision is already resolved (e.g. graph re-entered from a
    checkpoint after resume), do NOT call gate() again — re-interrupting would block
    forever waiting for a second resolution of an already-resolved review card.
    """
    if state.decision in ("approved", "rejected"):
        return state
    if not state.drafts:
        return state

    skipped = state.skipped_cooldown + state.skipped_stop_condition
    body = REVIEW_HEADER.format(
        run_date=state.run_date.isoformat(),
        n_total=len(state.drafts), n_email=len(state.drafts), skipped=skipped,
    )
    body += "\n\n" + "\n\n---\n\n".join(
        f"**To:** {d.contact_id}  |  **Template:** `{d.template_key}`\n\n"
        + (f"_Subject:_ {d.subject}\n\n" if d.subject else "")
        + d.body
        for d in state.drafts
    )
    item = ReviewItem(
        key=state.session_id or "batch",
        subject=f"CRM Nurture batch — {state.run_date.isoformat()} ({len(state.drafts)})",
        context_md=body,
        draft_ref={"kind": "nurture_batch", "draft_count": len(state.drafts)},
    )
    decisions = gate(
        Gate.OUTBOUND_TO_HUMANS,
        [item],
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        correlation_id=state.correlation_id,
    )
    state.decision = decisions.get(item.key, "rejected")  # type: ignore[assignment]
    log.info("nurture_hitl_decision", decision=state.decision, drafts=len(state.drafts))
    return state


@traced_node("send_or_discard")
def send_node(state: NurtureState) -> NurtureState:
    if state.decision != "approved":
        log.info("nurture_batch_rejected", count=len(state.drafts))
        return state

    candidates_by_id = {c.contact_id: c for c in state.candidates}
    for draft in state.drafts:
        candidate = candidates_by_id.get(draft.contact_id)
        if candidate is None:
            continue
        if not acquire_advisory_lock(draft.contact_id):
            log.info("nurture_skipped_locked", contact_id=str(draft.contact_id))
            continue
        try:
            provider_id = deliver(draft, candidate)
        except Exception as exc:  # noqa: BLE001 — delivery failures must not abort the batch; surface per-draft
            log.error("nurture_delivery_failed", contact_id=str(draft.contact_id),
                      channel=draft.channel, error=str(exc))
            state.record_error("send_or_discard", exc)
            draft.approved = False
            state.failed_count += 1
            continue
        draft.sent_provider_id = provider_id
        draft.approved = True
        log_outbound(draft, candidate, provider_id)
        state.sent_count += 1
    return state


@traced_node("emit")
def emit_node(state: NurtureState) -> NurtureState:
    if state.decision != "approved":
        return state
    for draft in state.drafts:
        if not draft.approved:
            continue
        emit_event(
            EventType.OUTREACH_EMAIL_SENT,
            source_agent=state.agent_name,
            payload={
                "contact_id": str(draft.contact_id),
                "channel": draft.channel,
                "template_key": draft.template_key,
                "provider_id": draft.sent_provider_id,
            },
            correlation_id=state.correlation_id,
        )
    return state


@traced_node("write_signals")
async def write_signals_node(state: NurtureState) -> NurtureState:
    """Write per-interaction outcome vectors to Pinecone outreach_signals namespace.

    Also upserts outreach_threads rows to maintain cross-channel touch counts.
    Runs after emit so we only index approved, sent messages.
    """
    if state.decision != "approved":
        return state

    candidates_by_id = {c.contact_id: c for c in state.candidates}

    for draft in state.drafts:
        if not draft.approved:
            continue
        candidate = candidates_by_id.get(draft.contact_id)
        persona = candidate.persona if candidate else "unknown"
        stage = candidate.stage if candidate else "new_lead"

        await write_outreach_signal(
            persona=persona,
            stage=stage,
            channel=draft.channel,
            template_key=draft.template_key,
            angle="nurture",
            reply_received=False,
            days_to_reply=-1,
            contact_id=str(draft.contact_id),
            agent_name=state.agent_name,
            run_id=str(state.run_id),
        )
        # Also populate the `emails` namespace so icp_scoring's semantic_pain_match
        # sub-score has live signal data to read. provider_id is the Gmail message_id.
        if draft.sent_provider_id:
            await write_email_signal(
                contact_id=str(draft.contact_id),
                event_type="sent",
                subject=draft.subject,
                body_preview=draft.body[:300],
                template_key=draft.template_key,
                persona=persona,
                stage=stage,
                provider_id=draft.sent_provider_id,
                agent_name=state.agent_name,
            )
        upsert_outreach_thread(str(draft.contact_id), draft.channel)
        state.rag_signals_written += 1

    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(NurtureState)
    g.add_node("parse_discord_intent", parse_discord_intent_node)
    g.add_node("load_candidates", load_node)
    g.add_node("filter_due", filter_node)
    g.add_node("rag_augment", rag_augment_node)
    g.add_node("draft", draft_node)
    g.add_node("hitl_gate", hitl_gate_node)
    g.add_node("send_or_discard", send_node)
    g.add_node("emit", emit_node)
    g.add_node("write_signals", write_signals_node)

    g.set_entry_point("parse_discord_intent")
    g.add_edge("parse_discord_intent", "load_candidates")
    g.add_edge("load_candidates", "filter_due")
    g.add_edge("filter_due", "rag_augment")
    g.add_edge("rag_augment", "draft")
    g.add_edge("draft", "hitl_gate")
    g.add_edge("hitl_gate", "send_or_discard")
    g.add_edge("send_or_discard", "emit")
    g.add_edge("emit", "write_signals")
    g.add_edge("write_signals", END)
    return g.compile(checkpointer=get_checkpointer())
