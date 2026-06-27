"""LangGraph for Offer Matching & Playbook (Agent #7).

Flow:
    load_hot_contacts
      → propose   (per contact: Claude Opus → service_package+demo+playbook+memo)
      → hitl_review  (founder approves/rejects the proposal batch)
      → hitl_wait
      → persist   (write approved proposals to opportunities + memo drafts)
      → emit      (one event per opportunity)
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.outreach.playbook_signals import write_playbook_signal
from omerion_core.telemetry.middleware import traced_node

from .state import OfferMatchingState
from .tools import (
    find_similar_wins,
    load_hot_contacts,
    summary_stats,
    synthesize_proposal,
    write_memo_draft,
    write_opportunity,
)

log = get_logger("omerion.agents.offer_matching")


@traced_node("parse_discord_intent")
def parse_discord_intent_node(state: OfferMatchingState) -> OfferMatchingState:
    """If triggered from Discord, target the contact(s) named in the prompt.

    Pass-through on event/cron runs (no discord_message). The downstream batch
    HITL gate still requires founder approval before any opportunity is written.
    """
    if not state.discord_message:
        return state
    from omerion_core.inbound.discord_intent import resolve_contact_targets

    ids, cost, instructions = resolve_contact_targets(state.discord_message)
    state.tokens_input += cost.get("tokens_input", 0)
    state.tokens_output += cost.get("tokens_output", 0)
    state.cost_usd += cost.get("cost_usd", 0.0)
    if ids:
        state.candidate_contact_ids = ids
        if instructions:
            state.scratch["custom_instructions"] = instructions
    return state


@traced_node("load_hot_contacts")
def load_node(state: OfferMatchingState) -> OfferMatchingState:
    state.hot_contacts = load_hot_contacts(state.candidate_contact_ids or None)
    log.info("offer_matching_loaded", count=len(state.hot_contacts))
    return state


@traced_node("propose")
def propose_node(state: OfferMatchingState) -> OfferMatchingState:
    if not state.hot_contacts:
        return state
    router = ClaudeRouter()
    for row in state.hot_contacts:
        contact_id = str(row.get("contact_id", ""))
        try:
            # Use cached Pinecone results on graph replay (post-HITL resume).
            # On first run the cache is empty; we populate it here and it is
            # persisted into the checkpoint so resume never re-queries Pinecone.
            if contact_id not in state.pinecone_cache:
                contact = row.get("contacts") or {}
                persona = contact.get("persona") or "unknown"
                rationale = row.get("rationale") or {}
                why_now = rationale.get("why_now") if isinstance(rationale, dict) else None
                account = contact.get("accounts") or {}
                pain = [why_now] if why_now else ([account.get("pain_signal")] if account.get("pain_signal") else [])
                state.pinecone_cache[contact_id] = find_similar_wins(persona, [p for p in pain if p])
            state.proposals.append(synthesize_proposal(router, row, cached_similar=state.pinecone_cache[contact_id]))
        except Exception as exc:  # noqa: BLE001
            log.error("offer_synth_failed", contact_id=contact_id, error=str(exc))
    return state


@traced_node("hitl_review")
def hitl_review_node(state: OfferMatchingState) -> OfferMatchingState:
    """Route the entire proposal batch to founder for approval before CRM write."""
    if not state.proposals:
        return state

    stats = summary_stats(state.proposals)
    body_md = (
        f"### Offer Matching Batch — {len(state.proposals)} proposals\n\n"
        f"**Avg value:** ${stats['avg_value']:,.0f}  |  "
        f"**Packages:** {', '.join(f'{k}: {v}' for k, v in stats['packages'].items())}\n\n"
        "---\n\n"
    )
    body_md += "\n\n---\n\n".join(
        f"**Contact:** {p.contact_id}  |  **Persona:** {p.persona} (tier {p.persona_tier})\n\n"
        f"**Package:** `{p.service_package}`  →  Demo: `{p.demo_reference}`\n\n"
        f"**Value:** ${p.value_est_usd:,.0f}  |  **Confidence:** {p.confidence:.2f}\n\n"
        f"**Rationale:** {p.rationale}\n\n"
        f"**Memo:**\n{p.memo_md[:500]}"
        for p in state.proposals
    )

    review = create_founder_review_task(
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        subject=f"Offer matching batch — {len(state.proposals)} proposals (${stats['avg_value']:,.0f} avg)",
        context_md=body_md,
        draft_ref={"kind": "offer_batch", "proposal_count": len(state.proposals)},
        correlation_id=state.correlation_id,
    )
    state.review_id = review["review_id"]
    state.hitl_review_id = review["review_id"]
    return state


@traced_node("hitl_wait")
def hitl_wait_node(state: OfferMatchingState) -> OfferMatchingState:
    if not state.review_id:
        return state
    # Replay guard: if a decision is already cached on state (e.g. from a prior resume),
    # don't re-call interrupt() — that would block forever waiting for a second resolution
    # of an already-resolved review.
    if state.decision in ("approved", "rejected"):
        return state
    result = interrupt({"review_id": str(state.review_id), "session_id": state.session_id})
    decisions = result.get("decisions", {})
    state.decision = decisions.get(str(state.review_id), "rejected")
    state.scratch["decision_notes"] = result.get("decision_notes")
    return state


@traced_node("persist")
def persist_node(state: OfferMatchingState) -> OfferMatchingState:
    if state.decision != "approved":
        log.info("offer_batch_rejected", count=len(state.proposals))
        return state
    for p in state.proposals:
        opportunity_id = write_opportunity(p)
        if opportunity_id is None:
            continue
        write_memo_draft(p, opportunity_id)
        state.scratch.setdefault("opportunity_ids", []).append(str(opportunity_id))
        state.opportunities_created += 1
    return state


@traced_node("emit")
def emit_node(state: OfferMatchingState) -> OfferMatchingState:
    if state.decision != "approved" or not state.opportunities_created:
        return state
    stats = summary_stats(state.proposals)
    opportunity_ids = state.scratch.get("opportunity_ids", [])
    emit_event(
        EventType.PROPOSAL_READY,
        source_agent=state.agent_name,
        payload={"opportunity_ids": opportunity_ids, "stats": stats},
        correlation_id=state.correlation_id,
    )
    high_conf = [p for p in state.proposals if p.confidence >= 0.75]
    if high_conf:
        emit_event(
            EventType.PROPOSAL_DRAFT_READY,
            source_agent=state.agent_name,
            payload={
                "opportunity_ids": opportunity_ids,
                "high_confidence_count": len(high_conf),
                "max_confidence": max(p.confidence for p in high_conf),
                "stats": stats,
            },
            correlation_id=state.correlation_id,
        )

    # Populate `playbooks` namespace so future proposals have RAG context.
    # opportunity_ids list is parallel to proposals (persist_node appends in order).
    opp_id_iter = iter(state.scratch.get("opportunity_ids", []))
    for p in state.proposals:
        opp_id = next(opp_id_iter, None)
        if not opp_id or not p.service_package:
            continue
        write_playbook_signal(
            opportunity_id=opp_id,
            contact_id=str(p.contact_id),
            persona=p.persona,
            service_package=p.service_package,
            demo_reference=p.demo_reference or "",
            value_bucket=p.value_bucket,
            rationale=p.rationale,
            memo_preview=p.memo_md[:300],
            pain_signals=[p.rationale] if p.rationale else [],
            confidence=p.confidence,
            agent_name=state.agent_name,
        )
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(OfferMatchingState)
    g.add_node("parse_discord_intent", parse_discord_intent_node)
    g.add_node("load_hot_contacts", load_node)
    g.add_node("propose", propose_node)
    g.add_node("hitl_review", hitl_review_node)
    g.add_node("hitl_wait", hitl_wait_node)
    g.add_node("persist", persist_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("parse_discord_intent")
    g.add_edge("parse_discord_intent", "load_hot_contacts")
    g.add_edge("load_hot_contacts", "propose")
    g.add_edge("propose", "hitl_review")
    g.add_edge("hitl_review", "hitl_wait")
    g.add_edge("hitl_wait", "persist")
    g.add_edge("persist", "emit")
    g.add_edge("emit", END)
    return g.compile(checkpointer=get_checkpointer())
