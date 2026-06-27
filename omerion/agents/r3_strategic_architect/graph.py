"""LangGraph for R3 Strategic Architect.

Flow:
    load_signals → [route: signals empty → END]
                 → retrieve_prior → synthesize → persist
                 → hitl_review → hitl_wait → emit
    retrieve_prior  : semantic recall of R3's own prior proposals (intelligence_r3)
    emit            : fires RD_PROPOSAL_SUBMITTED for approved + writes every decided
                      proposal back into intelligence_r3 (continuous-improvement loop)
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .prompts import REVIEW_HEADER
from .signals import (
    format_prior_block,
    retrieve_prior_proposals,
    write_proposal_signal,
)
from .state import ArchitectState
from .tools import (
    load_signals,
    mark_proposal_decision,
    synthesize_proposals_clustered,
    write_proposal,
)

log = get_logger("omerion.agents.r3_strategic_architect")


@traced_node("load_signals")
def load_node(state: ArchitectState) -> ArchitectState:
    state.signals = load_signals(state.lookback_days)
    log.info(
        "r3_signals_loaded",
        insights=len(state.signals.rd_insights),
        oss=len(state.signals.oss_candidates),
        reports=len(state.signals.attribution_reports),
    )
    return state


def _route_after_load(state: ArchitectState) -> str:
    total = (
        len(state.signals.rd_insights)
        + len(state.signals.oss_candidates)
        + len(state.signals.attribution_reports)
    )
    if total == 0:
        log.warning("r3_no_signals_skip", lookback_days=state.lookback_days)
        return "end"
    return "synthesize"


@traced_node("retrieve_prior")
def retrieve_prior_node(state: ArchitectState) -> ArchitectState:
    """Semantic recall of R3's own decided proposals near the current signal landscape.

    Builds a query from the loaded signal titles, retrieves the top-3 most similar
    prior proposals (agent_id-scoped), and renders them into a <=150-token block that
    seeds synthesis: APPROVED matches are precedent to extend, REJECTED are to avoid.
    Fails open — empty recall just means synthesis runs with no memory this cycle.
    """
    insight_titles = " ".join(
        str(r.get("title", "")) for r in state.signals.rd_insights[:10]
    )
    oss_names = " ".join(
        str(r.get("name", "")) for r in state.signals.oss_candidates[:10]
    )
    query_text = f"{insight_titles} {oss_names}".strip()
    priors = retrieve_prior_proposals(query_text)
    state.prior_block = format_prior_block(priors)
    log.info("r3_prior_retrieved", count=len(priors))
    return state


@traced_node("synthesize")
def synthesize_node(state: ArchitectState) -> ArchitectState:
    router = ClaudeRouter()
    # If triggered from Discord (#arch), steer the synthesis toward the founder's
    # request by appending it to the free-text prior_block the prompt already
    # consumes. Additive — a no-op on event/cron runs (discord_message is None),
    # so the existing batch-synthesis behaviour is unchanged.
    prior_block = state.prior_block
    if state.discord_message:
        prior_block = (prior_block or "") + (
            "\n\n[FOUNDER FOCUS — from Discord #arch]: prioritise design proposals that "
            f"address this request: {state.discord_message.strip()}"
        )
    state.proposals = synthesize_proposals_clustered(
        router=router,
        signals=state.signals,
        lookback_days=state.lookback_days,
        run_date=state.run_date.isoformat(),
        prior_block=prior_block,
    )
    # Distinguish "genuinely nothing to propose" from a silent synthesis/JSON
    # failure: load_signals already routed to END when there were zero signals,
    # so reaching here with signals but zero proposals almost always means the
    # Opus call errored or returned unparseable JSON (synthesize fails open to []).
    _signal_total = (
        len(state.signals.rd_insights)
        + len(state.signals.oss_candidates)
        + len(state.signals.attribution_reports)
    )
    if not state.proposals and _signal_total > 0:
        log.warning(
            "r3_synthesis_empty_despite_signals",
            insights=len(state.signals.rd_insights),
            oss=len(state.signals.oss_candidates),
            reports=len(state.signals.attribution_reports),
        )
    log.info("r3_proposals_synthesized", count=len(state.proposals))
    return state


@traced_node("persist")
def persist_node(state: ArchitectState) -> ArchitectState:
    for p in state.proposals:
        p.proposal_id = write_proposal(p)
        if p.proposal_id is not None:
            state.proposals_written += 1
    return state


@traced_node("hitl_review")
def hitl_review_node(state: ArchitectState) -> ArchitectState:
    # Replay-safety: if this node re-runs after a crash before hitl_wait's
    # interrupt checkpoint, a fresh INSERT per proposal would create duplicate
    # founder cards. Reuse existing pending reviews for this session, matched by
    # proposal_id in draft_ref.
    from omerion_core.clients.supabase_client import supabase
    existing_by_proposal: dict[str, str] = {}
    try:
        resp = (
            supabase.table("founder_review_queue")
            .select("review_id,draft_ref")
            .eq("session_id", state.session_id or "")
            .eq("agent_name", state.agent_name)
            .eq("decision", "pending")
            .execute()
        )
        for row in (resp.data or []):
            ref = row.get("draft_ref") or {}
            pid = ref.get("proposal_id") if isinstance(ref, dict) else None
            if pid:
                existing_by_proposal[str(pid)] = row["review_id"]
    except Exception as exc:  # noqa: BLE001
        log.warning("r3_existing_reviews_lookup_failed", error=str(exc))

    for p in state.proposals:
        if p.proposal_id is None:
            continue
        if str(p.proposal_id) in existing_by_proposal:
            p.review_id = existing_by_proposal[str(p.proposal_id)]  # type: ignore[assignment]
            log.info("r3_hitl_review_reused", proposal_id=str(p.proposal_id), review_id=str(p.review_id))
            continue
        header = REVIEW_HEADER.format(
            title=p.title,
            target_module=p.target_module,
            impact=p.impact,
            effort=p.effort,
            priority_score=p.priority_score,
        )
        body = (
            f"{header}\n\n**Problem**\n\n{p.problem_statement}\n\n"
            f"**Hypothesis**\n\n{p.hypothesis}\n\n"
            f"**Design doc**\n\n{p.design_doc_md}\n\n"
            f"**Blueprint handoff**\n```json\n{p.blueprint_handoff}\n```"
        )
        review = create_founder_review_task(
            agent_name=state.agent_name,
            session_id=state.session_id or "",
            subject=f"R3 Proposal — {p.title}",
            context_md=body,
            draft_ref={"kind": "rd_proposal", "proposal_id": str(p.proposal_id)},
            correlation_id=state.correlation_id,
        )
        p.review_id = review["review_id"]
    state.hitl_review_ids = [str(p.review_id) for p in state.proposals if p.review_id]
    state.hitl_review_id = state.hitl_review_ids[-1] if state.hitl_review_ids else None
    return state


@traced_node("hitl_wait")
def hitl_wait_node(state: ArchitectState) -> ArchitectState:
    review_ids = [str(p.review_id) for p in state.proposals if p.review_id]
    if not review_ids:
        return state
    result = interrupt({"review_ids": review_ids, "session_id": state.session_id})
    decisions = result.get("decisions", {}) if isinstance(result, dict) else {}
    if not decisions:
        log.warning(
            "r3_hitl_no_decisions",
            session_id=state.session_id,
            review_ids=review_ids,
            hint="HITL timed out or returned empty — all proposals will be rejected",
        )
    for p in state.proposals:
        if not p.review_id:
            continue
        decision = decisions.get(str(p.review_id), "rejected")
        p.decision = "approved" if decision == "approved" else "rejected"
        if p.proposal_id is not None:
            mark_proposal_decision(p.proposal_id, p.decision)
    return state


@traced_node("emit")
def emit_node(state: ArchitectState) -> ArchitectState:
    for p in state.proposals:
        # Write-back to semantic memory: index every *decided* proposal (approved
        # OR rejected) into the intelligence_r3 namespace so future runs recall it.
        # This is the continuous-improvement loop — the vault/Pinecone get smarter
        # with each founder decision. Idempotent on proposal_id; fails open.
        if p.decision in ("approved", "rejected") and p.proposal_id is not None:
            write_proposal_signal(p, agent_name=state.agent_name)
            state.proposals_embedded += 1

        if p.decision != "approved" or p.proposal_id is None:
            continue
        emit_event(
            EventType.RD_PROPOSAL_SUBMITTED,
            source_agent=state.agent_name,
            payload={
                "proposal_id": str(p.proposal_id),
                "title": p.title,
                "target_module": p.target_module,
                "impact": p.impact,
                "effort": p.effort,
                "priority_score": p.priority_score,
                "blueprint_handoff": p.blueprint_handoff,
            },
            correlation_id=state.correlation_id,
        )
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(ArchitectState)
    g.add_node("load_signals", load_node)
    g.add_node("retrieve_prior", retrieve_prior_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("persist", persist_node)
    g.add_node("hitl_review", hitl_review_node)
    g.add_node("hitl_wait", hitl_wait_node)
    g.add_node("emit", emit_node)
    g.set_entry_point("load_signals")
    g.add_conditional_edges(
        "load_signals",
        _route_after_load,
        {"synthesize": "retrieve_prior", "end": END},
    )
    g.add_edge("retrieve_prior", "synthesize")
    g.add_edge("synthesize", "persist")
    g.add_edge("persist", "hitl_review")
    g.add_edge("hitl_review", "hitl_wait")
    g.add_edge("hitl_wait", "emit")
    g.add_edge("emit", END)
    return g.compile(checkpointer=get_checkpointer())
