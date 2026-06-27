"""LangGraph for LinkedIn Outreach (Agent #4).

Flow:
    load_cohort
      → plan_steps          (cold/warm classification + cooldown gating)
      → apply_caps          (daily connection / DM caps from agents.yaml)
      → draft               (Claude Sonnet, one draft per planned step)
      → hitl_review         (founder approves the entire batch via Sheets)
      → hitl_wait
      → send_or_discard     (queue approved drafts for the LinkedIn sender)
      → emit
"""
from __future__ import annotations

from uuid import uuid4

from langgraph.graph import END, StateGraph

from langgraph.types import interrupt

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .prompts import REVIEW_CONTEXT_HEADER
from .state import LinkedInOutreachState
from omerion_core.outreach.signals import (
    query_outreach_signals,
    upsert_outreach_thread,
    write_outreach_signal,
)

from .tools import (
    apply_daily_caps,
    draft_message,
    load_cohort,
    log_activity,
    plan_steps,
    queue_for_sender,
    send_queued_messages,
)

log = get_logger("omerion.agents.linkedin_outreach")


@traced_node("parse_discord_intent")
def parse_discord_intent_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    """If triggered from Discord, target the contact(s) named in the prompt.

    Pass-through on event/cron runs (no discord_message). The downstream G1 HITL
    gate still requires founder approval before any DM is sent.
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


@traced_node("load_cohort")
def load_cohort_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    state.cohort = load_cohort(state.candidate_contact_ids or None)
    log.info("li_cohort_loaded", count=len(state.cohort))
    return state


@traced_node("plan_steps")
def plan_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    state.planned = plan_steps(state.cohort)
    log.info("li_steps_planned", count=len(state.planned))
    return state


@traced_node("apply_caps")
def caps_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    state.planned, state.skipped_capped = apply_daily_caps(state.planned)
    log.info("li_caps_applied", remaining=len(state.planned), skipped=state.skipped_capped)
    return state


@traced_node("rag_augment")
async def rag_augment_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    """Query outreach_signals Pinecone namespace for successful past patterns.

    Injects rag_context into each PlannedStep so the draft node can reference
    what messaging angles worked for this persona/stage combination.
    Pinecone outage degrades gracefully: drafts proceed without RAG context
    rather than aborting the entire batch.
    """
    if not state.planned:
        return state
    for step in state.planned:
        # Use step_type (connection_request / dm) as the stage discriminator so
        # the RAG query retrieves signals specific to this step, not all "contacted"
        # signals regardless of step position. This activates per-step signal
        # learning: connection requests retrieve connection-request win patterns;
        # ask DMs retrieve angles that drove replies at the ask stage.
        try:
            step.rag_context = await query_outreach_signals(step.persona, step.step_type)
        except Exception as exc:  # noqa: BLE001
            log.warning("li_rag_augment_failed", persona=step.persona,
                        step_type=step.step_type, error=str(exc), exc_info=True)
            step.rag_context = ""
    return state


@traced_node("draft")
def draft_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    if not state.planned:
        return state
    router = ClaudeRouter()
    for s in state.planned:
        try:
            state.drafts.append(draft_message(router, s))
        except Exception as exc:  # noqa: BLE001 — one failed draft must not abort the batch
            log.error("li_draft_error", step_id=str(s.step_id), error=str(exc))
            state.record_error("draft", exc)
    return state


@traced_node("hitl_review")
def hitl_review_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    if not state.drafts:
        return state
    body_md = REVIEW_CONTEXT_HEADER.format(
        run_date=state.run_date.isoformat(),
        n=len(state.drafts),
        capped=state.skipped_capped,
        stopped=state.skipped_stopped,
    )
    body_md += "\n\n" + "\n\n---\n\n".join(
        f"**To:** {d.contact_id}  |  **Track:** {d.track}  |  **Template:** `{d.template_key}`\n\n{d.body}"
        for d in state.drafts
    )
    review = create_founder_review_task(
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        subject=f"LinkedIn outreach batch — {state.run_date.isoformat()} ({len(state.drafts)} drafts)",
        context_md=body_md,
        draft_ref={"kind": "linkedin_batch", "draft_count": len(state.drafts)},
        correlation_id=state.correlation_id,
    )
    state.review_id = review["review_id"]
    state.hitl_review_id = review["review_id"]
    return state


@traced_node("hitl_wait")
def hitl_wait_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    if not state.review_id:
        return state
    # Replay guard: if a decision is already cached on state (e.g. the graph is
    # re-entered from a checkpoint after the resume value was applied), do NOT
    # call interrupt() again — re-interrupting would block forever waiting for a
    # second resolution of an already-resolved review. For this G1 sender that
    # guard is critical: it keeps a resumed/replayed run from re-pausing and,
    # combined with the send-path idempotency, from re-touching contacts.
    if state.decision in ("approved", "rejected"):
        return state
    result = interrupt({"review_id": str(state.review_id), "session_id": state.session_id})
    decisions = result.get("decisions", {})
    state.decision = decisions.get(str(state.review_id), "rejected")
    state.scratch["decision_notes"] = result.get("decision_notes")
    return state


@traced_node("send_or_discard")
def send_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    if state.decision != "approved":
        log.info("li_batch_rejected", count=len(state.drafts))
        return state
    sequence_id = uuid4()
    for draft in state.drafts:
        comm_id = queue_for_sender(draft, sequence_id, sequence_step=0)
        log_activity(draft.contact_id, comm_id, "linkedin_queued",
                     metadata={"template_key": draft.template_key, "track": draft.track})
        draft.approved = True
        state.sent_count += 1
    return state


@traced_node("send_messages")
def send_messages_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    if state.decision != "approved" or not state.sent_count:
        return state
    try:
        result = send_queued_messages(limit=state.sent_count)
        log.info("li_playwright_drain", **result)
    except Exception as exc:  # noqa: BLE001
        log.warning("li_playwright_drain_failed", error=str(exc))
    return state


@traced_node("emit")
def emit_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    if state.decision != "approved" or not state.sent_count:
        return state
    for draft in state.drafts:
        if not draft.approved:
            continue
        emit_event(
            EventType.OUTREACH_LI_SENT,
            source_agent=state.agent_name,
            payload={
                "contact_id": str(draft.contact_id),
                "template_key": draft.template_key,
                "track": draft.track,
                "step_type": draft.step_type,
            },
            correlation_id=state.correlation_id,
        )
    return state


@traced_node("write_signals")
async def write_signals_node(state: LinkedInOutreachState) -> LinkedInOutreachState:
    """Write per-interaction outcome vectors to Pinecone outreach_signals namespace.

    Also upserts outreach_threads row to maintain cross-channel touch counts.
    Runs after emit so we only index approved, sent messages.
    """
    if state.decision != "approved":
        return state

    planned_by_step_id = {s.step_id: s for s in state.planned}

    for draft in state.drafts:
        if not draft.approved:
            continue
        step = planned_by_step_id.get(draft.step_id)
        persona = step.persona if step else "unknown"
        angle = step.persona_variant if step else "cold_outreach"

        await write_outreach_signal(
            persona=persona,
            stage="contacted",
            channel="linkedin_dm",
            template_key=draft.template_key,
            angle=angle,
            reply_received=False,
            days_to_reply=-1,
            contact_id=str(draft.contact_id),
            agent_name=state.agent_name,
            run_id=str(state.run_id),
        )
        upsert_outreach_thread(str(draft.contact_id), "linkedin")
        state.rag_signals_written += 1

    return state


def build():
    g = StateGraph(LinkedInOutreachState)
    g.add_node("parse_discord_intent", parse_discord_intent_node)
    g.add_node("load_cohort", load_cohort_node)
    g.add_node("plan_steps", plan_node)
    g.add_node("apply_caps", caps_node)
    g.add_node("rag_augment", rag_augment_node)
    g.add_node("draft", draft_node)
    g.add_node("hitl_review", hitl_review_node)
    g.add_node("hitl_wait", hitl_wait_node)
    g.add_node("send_or_discard", send_node)
    g.add_node("send_messages", send_messages_node)
    g.add_node("emit", emit_node)
    g.add_node("write_signals", write_signals_node)

    g.set_entry_point("parse_discord_intent")
    g.add_edge("parse_discord_intent", "load_cohort")
    g.add_edge("load_cohort", "plan_steps")
    g.add_edge("plan_steps", "apply_caps")
    g.add_edge("apply_caps", "rag_augment")
    g.add_edge("rag_augment", "draft")
    g.add_edge("draft", "hitl_review")
    g.add_edge("hitl_review", "hitl_wait")
    g.add_edge("hitl_wait", "send_or_discard")
    g.add_edge("send_or_discard", "send_messages")
    g.add_edge("send_messages", "emit")
    g.add_edge("emit", "write_signals")
    g.add_edge("write_signals", END)
    from omerion_core.runtime.checkpointer import get_checkpointer
    return g.compile(checkpointer=get_checkpointer())
