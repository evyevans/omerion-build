"""LangGraph for ICP Scoring (Agent #6).

Flow:
    load → score → persist → shortlist → digest → emit → END
"""
from __future__ import annotations

from uuid import UUID

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .state import ScoredContact, ScoringState
from .tools import (
    compute_fit,
    compute_intent,
    compute_timing,
    explain_intent,
    final_score,
    load_candidates,
    render_digest,
    reset_rag_breaker,
    segment_of,
    write_scores,
)

log = get_logger("omerion.agents.icp_scoring")


@traced_node("parse_discord_intent")
def parse_discord_intent_node(state: ScoringState) -> ScoringState:
    """If triggered from Discord, target the contact(s) named in the prompt.

    Pass-through on event/cron runs (no discord_message) → existing behaviour is
    byte-for-byte unchanged; the graph is not otherwise modified.
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


@traced_node("load_candidates")
def load_node(state: ScoringState) -> ScoringState:
    state.contacts = load_candidates(state.candidate_contact_ids or None)
    log.info("icp_candidates_loaded", count=len(state.contacts))
    return state


@traced_node("score")
def score_node(state: ScoringState) -> ScoringState:
    # Reset the per-batch Pinecone circuit breaker so a previous batch's failures
    # don't suppress this one. The breaker is module-level state on purpose
    # (it's per-batch, not per-contact), but that means we own resetting it.
    reset_rag_breaker()
    router = ClaudeRouter()
    for c in state.contacts:
        fit, fit_sub = compute_fit(c)
        intent, intent_sub = compute_intent(c)
        timing, timing_sub = compute_timing(c)
        final = final_score(fit, intent, timing, c.get("persona") or "default")
        segment = segment_of(final)
        signals_str = f"fit={fit_sub} intent={intent_sub} timing={timing_sub}"
        explanation = explain_intent(router, c, signals_str) if segment in ("hot", "warm") else ""
        state.scored.append(ScoredContact(
            contact_id=UUID(c["contact_id"]),
            account_id=UUID(c["account_id"]),
            persona=c.get("persona") or "unknown",
            fit=fit,
            intent=intent,
            timing=timing,
            final=final,
            segment=segment,
            explanations={"why_now": explanation} if explanation else {},
        ))
    # Raw Supabase rows are no longer needed after scoring; clear them so the
    # checkpoint blob for subsequent nodes only carries scored results.
    state.contacts = []
    return state


@traced_node("persist")
def persist_node(state: ScoringState) -> ScoringState:
    written = write_scores(state.run_date, state.scored)
    log.info("icp_scores_written", count=written, run_date=str(state.run_date))
    return state


@traced_node("shortlist")
def shortlist_node(state: ScoringState) -> ScoringState:
    limit = settings.agent("icp_scoring").get("max_shortlist_size", 15)
    state.scored.sort(key=lambda s: s.final, reverse=True)
    state.shortlist = [s for s in state.scored if s.segment in ("hot", "warm")][:limit]
    return state


@traced_node("digest")
def digest_node(state: ScoringState) -> ScoringState:
    router = ClaudeRouter()
    body = render_digest(router, state.run_date, state.shortlist)
    state.scratch["digest_md"] = body
    # Row in founder_review_queue with decision='pending' would overload the queue;
    # the digest is informational — surfaced via Sheets `Daily Digest` tab + 06:30 email.
    from omerion_core.clients.supabase_client import supabase
    supabase.table("generated_drafts").insert({
        "agent_name": state.agent_name,
        "purpose": "daily_digest",
        "draft_body": body,
        "draft_metadata": {
            "reasons": "daily_scoring_logic",
            "subject": f"ICP digest {state.run_date.isoformat()}",
            "correlation_id": str(state.correlation_id) if state.correlation_id else None,
        },
        "model": "claude-sonnet-4-6",
    }).execute()
    state.digest_sent = True
    return state


@traced_node("emit")
def emit_node(state: ScoringState) -> ScoringState:
    for s in state.scored:
        emit_event(
            EventType.CONTACT_SCORED,
            source_agent=state.agent_name,
            payload={
                "contact_id": str(s.contact_id),
                "account_id": str(s.account_id),
                "segment": s.segment,
                "final": round(s.final, 4),
            },
            correlation_id=state.correlation_id,
        )
    emit_event(
        EventType.FOUNDER_DAILY_DIGEST,
        source_agent=state.agent_name,
        payload={
            "run_date": state.run_date.isoformat(),
            "hot_count": sum(1 for s in state.scored if s.segment == "hot"),
            "warm_count": sum(1 for s in state.scored if s.segment == "warm"),
            "shortlist_size": len(state.shortlist),
        },
        correlation_id=state.correlation_id,
    )
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(ScoringState)
    g.add_node("parse_discord_intent", parse_discord_intent_node)
    g.add_node("load", load_node)
    g.add_node("score", score_node)
    g.add_node("persist", persist_node)
    g.add_node("shortlist", shortlist_node)
    g.add_node("digest", digest_node)
    g.add_node("emit", emit_node)
    g.set_entry_point("parse_discord_intent")
    g.add_edge("parse_discord_intent", "load")
    g.add_edge("load", "score")
    g.add_edge("score", "persist")
    g.add_edge("persist", "shortlist")
    g.add_edge("shortlist", "digest")
    g.add_edge("digest", "emit")
    g.add_edge("emit", END)
    return g.compile(checkpointer=get_checkpointer())
