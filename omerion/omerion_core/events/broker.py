"""Omerion event broker — wires the 15-agent handoff chain.

When an upstream agent calls emit_event(), the broker receives the INSERT via
Supabase Realtime and automatically triggers every downstream agent that
declares the event in its events_consumed frontmatter.

Each trigger runs in a daemon thread so the Realtime callback returns instantly
and multiple downstream agents fire in parallel (e.g., contact.scored fans out
to linkedin-outreach + crm-nurture + offer-matching simultaneously).

Every handoff is written to agent_messages so the Discord #omerion-room
narration and the dashboard team-chat panel both have a full audit trail.

Pattern proven by: omerion_core/outreach/tracker.py:register_reply_listener()
"""
from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

from omerion_core.clients.supabase_client import supabase
from omerion_core.events.bus import EventType, subscribe
from omerion_core.logging import get_logger
from omerion_core.runtime import run_lifecycle
from omerion_core.runtime.run_executor import execute_run

log = get_logger("omerion.events.broker")

# Wave 7.0 (Musk audit blocker #1): hard ceiling on event-handoff chain depth.
#
# Without this cap, a misconfigured HITL gate or a future skill.md edit that
# emits its own trigger event creates an undetectable infinite loop. The
# known cycle today is:
#   automation.blueprint.approved → spec-architect → BUILD_TASK_CREATED → builder
#   → DEPLOYMENT_LIVE → deployer → DEPLOYMENT_HEALTH_CONFIRMED
#   → outcome-attribution → ATTRIBUTION_REPORT_READY → r3-strategic-architect
#   → RD_PROPOSAL_SUBMITTED → (terminal: founder-gated, no auto-build consumer)
#
# 20 hops covers any legitimate chain (longest observed in production
# is ~7) while terminating runaway loops within seconds.
MAX_HOPS: int = 20

# ── Canonical event → downstream agent map ───────────────────────────────────
# Derived from EventType enum (authoritative) + skill.md frontmatter (declarative).
# Agent names are kebab-case registry keys matching omerion_core/runtime/registry.py.
#
# Intentionally excluded:
#   OUTREACH_REPLIED     — handled by outreach/tracker.py:register_reply_listener()
#   MEETING_TRANSCRIPT_RECEIVED — Fireflies webhook triggers INTEL via HTTP directly
#   opportunity.created  — not yet in EventType enum (skill.md inconsistency)
#   contact.engagement   — not yet in EventType enum
#   R-series as triggers — managed_agent_cron; their emits do flow through this map

EVENT_SUBSCRIPTIONS: dict[str, list[str]] = {
    # MAPPER emits → SCOUT enriches contacts from the new account batch
    EventType.ACCOUNT_BATCH_READY.value:      ["lead-scraper"],

    # SCOUT emits → SCORE classifies and scores all new contacts
    EventType.CONTACT_ENRICHED.value:         ["icp-scoring"],

    EventType.CONTACT_SCORED.value:           ["linkedin-outreach", "crm-nurture", "offer-matching"],

    "factory.payment.confirmed":              ["factory-intake"],
    EventType.CLIENT_PROFILE_READY.value:     ["automation-strategist"],
    # meeting-intelligence emits this after processing a Fireflies transcript;
    # client-intake extracts the structured client profile from the draft blueprint.
    EventType.BLUEPRINT_DRAFT_CREATED.value:  ["client-intake"],
    "proposal.accepted":                      [],  # client-onboarding migrated to Claude Dev cloud (2026-06-21)
    EventType.CLIENT_INTAKE_GAPS_DETECTED.value: [],  # surfaces to founder via Discord alert; no auto-consumer
    # STRATEGIST founder-approved blueprint → SPEC decomposes it into build tasks,
    # POLISHER prepares the client-facing artifact (parallel consumers).
    "automation.blueprint.approved":          ["spec-architect", "executive-polisher"],
    "automation.blueprint.polished":          ["diagram-delivery"],

    # SPEC_ARCHITECT creates the deployment + build_tasks (from
    # automation.blueprint.approved above) → BUILDER autonomously codes each task.
    # (`build_orchestrator` was deleted 2026-06-15: it crashed on every
    # BLUEPRINT_APPROVED trigger — it required a deployment_id the event never
    # carries — and duplicated the live spec_architect → builder pipeline.)
    EventType.BUILD_TASK_CREATED.value:       [],  # builder migrated to Claude Dev cloud (2026-06-21)

    # BUILDER task failure → RAG ingests failure lessons for future runs
    EventType.BUILD_TASK_FAILED.value:        ["factory-rag"],

    # INTEL (meeting-driven) blueprint signed off → RAG indexes the approved blueprint
    # AND spec-architect decomposes it into build tasks. Decided 2026-06-15:
    # meeting-approved blueprints now auto-build (intentional). This is safe because
    # BlueprintApproved carries blueprint_id (required) + client_id (optional), and
    # event_ingress maps both into spec-architect state regardless of event_type; if
    # blueprint_id were ever missing, spec_architect's load node returns
    # validation_errors (no crash) rather than failing like the deleted
    # build_orchestrator did.
    EventType.BLUEPRINT_APPROVED.value:       ["factory-rag", "spec-architect"],

    # R3 strategic proposal submitted → intentionally founder-gated/manual (decided
    # 2026-06-15). The RD_PROPOSAL_SUBMITTED payload is a proposal shape that carries
    # no blueprint_id, so auto-routing it to spec-architect would no-op. Wiring it to
    # auto-build needs a proposal→blueprint adapter (future work). Leave unsubscribed.
    EventType.RD_PROPOSAL_SUBMITTED.value:    [],

    # BUILD deploys live → DEPLOYER provisions and smoke tests + CLIENT COMMS sends confirmation
    # + COMPLIANCE_CHECKER enforces business rules + SECURITY_AUDITOR scans for new findings.
    # deployer migrated to Claude Dev cloud (2026-06-21) — dropped from subscribers
    EventType.DEPLOYMENT_LIVE.value:          ["client-comms", "compliance-checker", "security-auditor"],

    # DEPLOYER smoke test passes → RAG ingests success record
    # (outcome-attribution migrated to Claude Dev cloud (2026-06-21) — dropped)
    EventType.DEPLOYMENT_HEALTH_CONFIRMED.value: ["factory-rag"],

    # ATTR report ready → R3 refines strategy with outcome data
    EventType.ATTRIBUTION_REPORT_READY.value: ["r3-strategic-architect"],

    # R1 emits per-insight → R2 scouts OSS (insight-seeded).
    # R3 is NO LONGER subscribed here: it's a strategic synthesizer over a 14-day
    # window, so re-running an Opus synthesis (+ founder HITL cards) on every single
    # insight is wasteful. R3 wakes on its cron + batch/sparse events below instead.
    EventType.RD_INSIGHT_CREATED.value:       ["r2-oss-scout"],

    # OSS_CANDIDATE_SCORED is per-candidate (high frequency) — also dropped from R3
    # for the same reason; ANALYSIS_READY (R2 per-batch heartbeat) keeps R3 in the loop.
    EventType.OSS_CANDIDATE_SCORED.value:     [],
    EventType.ANALYSIS_READY.value:           ["r3-strategic-architect"],

    # Analyst dossier written → R3 incorporates new research into strategy
    EventType.DOSSIER_READY.value:            ["r3-strategic-architect"],

    # Offer Matching high-confidence proposal draft → intentionally founder-gated/manual
    # (decided 2026-06-15). The PROPOSAL_DRAFT_READY payload is an opportunity/proposal
    # shape that carries no blueprint_id, so auto-routing it to spec-architect would
    # no-op. Wiring it to auto-build needs a proposal→blueprint adapter (future work).
    # Leave unsubscribed.
    EventType.PROPOSAL_DRAFT_READY.value:     [],

    # healer/auditor/trainer/qa-tester migrated to Claude Dev cloud (2026-06-21).
    # Their RSI/QA loops now run in the cloud; local subscribers cleared.
    EventType.REGRESSION_ALERT.value:         [],
    EventType.HEALING_APPLIED.value:          [],
    EventType.PROMPT_UPDATE_APPLIED.value:    [],
    EventType.BUILD_TASK_COMPLETED.value:     [],

    # Client onboarded → COMPLIANCE_CHECKER enforces baseline rules immediately.
    EventType.CLIENT_ONBOARDED.value:         ["compliance-checker"],
}


# ── Supabase write helpers ────────────────────────────────────────────────────

class HandoffLogWriteFailed(RuntimeError):
    """Wave 2.8: raised when the agent_messages audit row can't be written.

    Per the operating laws (plan §10 rule 11), no event may be emitted
    unless `_write_handoff_message` succeeds. A silent drop of the
    handoff record would make a downstream agent run invisible from the
    dashboard + Mission Control narration — and on a partial outage that
    invisibility compounds because the operator doesn't know that
    handoffs are missing rather than not happening.
    """


def _current_hop_count(correlation_id: str | None) -> int:
    """Return the deepest hop_count already recorded for this chain.

    Returns -1 when the chain has no prior handoffs (so the first handoff
    is written as hop_count=0). A query failure returns 0 to fail-safe
    open — losing the cap is preferable to losing all event traffic.
    """
    if not correlation_id:
        return -1
    try:
        rows = (
            supabase.table("agent_messages")
            .select("hop_count")
            .eq("correlation_id", correlation_id)
            .order("hop_count", desc=True)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return -1
        return int(rows[0].get("hop_count") or 0)
    except Exception as exc:
        log.warning("hop_count_lookup_failed", correlation_id=correlation_id, error=str(exc))
        return 0


def _write_handoff_message(
    *,
    from_agent: str,
    to_agent: str,
    event_type: str,
    run_id: str,
    correlation_id: str | None = None,
    hop_count: int = 0,
    meta: dict[str, Any] | None = None,
) -> None:
    """Insert the handoff audit row. **Raises** on failure (Wave 2.8).

    Wave 3.4: `correlation_id` is now a proper column so dashboards can
    render the per-chain timeline with a single indexed lookup instead
    of mining the JSONB meta column.

    The caller catches HandoffLogWriteFailed in `_handle_event` and
    refuses to spawn the downstream agent — better to skip a run than
    to run it invisibly.
    """
    row: dict[str, Any] = {
        "from_agent": from_agent or "unknown",
        "to_agent": to_agent,
        "message": f"{from_agent} → {to_agent} via {event_type}",
        "event_type": event_type,
        "run_id": run_id,
        "hop_count": hop_count,
        "meta": meta or {},
    }
    if correlation_id:
        row["correlation_id"] = correlation_id
    try:
        supabase.table("agent_messages").insert(row).execute()
    except Exception as exc:
        log.error(
            "handoff_message_write_failed_blocking",
            from_agent=from_agent,
            to_agent=to_agent,
            event_type=event_type,
            error=str(exc),
        )
        raise HandoffLogWriteFailed(
            f"handoff log write failed: {from_agent} → {to_agent} via {event_type}: {exc}"
        ) from exc


def _log_error(message: str, *, source: str = "event_broker", traceback: str | None = None, **meta: Any) -> None:
    try:
        supabase.table("error_log").insert({
            "source": source,
            "message": message,
            "traceback": traceback,
            "meta": meta,
        }).execute()
    except Exception:
        pass  # error logging must never raise


def _enqueue_dead_letter(
    *,
    event_type: str,
    payload: dict[str, Any],
    source_agent: str,
    target_agent: str,
    event_id: str | None,
    correlation_id: str | None,
    error: str,
) -> None:
    """Park a failed event→agent handoff in `event_dead_letter` for retry.

    Wave 7.1: before this existed, a broker dispatch failure (handoff-log write,
    create_run, or execute_run raising) wrote an `error_log` row and returned —
    the event was dropped permanently. The retry sweep in
    `sweeper.sweep_dead_letter_queue` had a consumer but NO producer, so it always
    found zero rows. This is that missing producer: it inserts a `pending` row
    (columns per migration 0041) keyed on (event_id, target_agent) so the sweep
    re-emits the event on its next tick (≤10 min).

    Never raises — a DLQ write failure must not break the broker daemon thread.
    Idempotent: the partial unique index on (event_id, target_agent) makes a
    repeated failure for the same pair a unique violation, which we swallow (the
    existing pending row is what the sweep retries). Re-emit is event-level, so
    consumers rely on their own idempotency keys to avoid duplicate side effects.
    """
    row: dict[str, Any] = {
        "event_type": event_type,
        "target_agent": target_agent,
        "source_agent": source_agent or "unknown",
        "payload": payload or {},
        "last_error": (error or "")[:2000],
        # status / next_retry_at / attempt_count / max_attempts use DB defaults
        # (pending / now() / 0 / 5).
    }
    if event_id:
        row["event_id"] = event_id
    if correlation_id:
        row["correlation_id"] = correlation_id
    try:
        supabase.table("event_dead_letter").insert(row).execute()
        log.info("event_dead_lettered", event_type=event_type, target_agent=target_agent)
    except Exception as exc:
        log.error(
            "dead_letter_write_failed",
            event_type=event_type,
            target_agent=target_agent,
            error=str(exc),
        )


# ── Core trigger logic ────────────────────────────────────────────────────────

def _handle_event(event_row: dict[str, Any], agent_name: str) -> None:
    """Create an agent_run record and execute it. Runs in a daemon thread."""
    event_type   = event_row.get("type", "")
    source_agent = event_row.get("source_agent", "unknown")
    payload      = event_row.get("payload") or {}
    event_id     = str(event_row.get("event_id", ""))

    run_id = str(uuid4())
    log.info(
        "broker_trigger_started",
        from_agent=source_agent,
        to_agent=agent_name,
        event_type=event_type,
        run_id=run_id,
    )

    # 1. Record the handoff in agent_messages (feeds #omerion-room + dashboard)
    #    Wave 2.8: a failed audit-row write blocks the run rather than
    #    silently emitting an invisible handoff. The operator notices the
    #    error in error_log; a downstream agent never quietly runs without
    #    a paper trail.
    #
    #    Wave 3.4: propagate correlation_id from the original event row so
    #    the end-to-end chain (trigger → emit → next agent → next emit) can
    #    be reconstructed via a single indexed query.
    event_correlation_id = event_row.get("correlation_id")
    correlation_str = str(event_correlation_id) if event_correlation_id else None

    # Wave 7.0: refuse to spawn when the chain has already hit MAX_HOPS.
    # This is the circuit breaker against undetectable infinite loops.
    next_hop = _current_hop_count(correlation_str) + 1
    if next_hop >= MAX_HOPS:
        log.error(
            "broker_hop_cap_exceeded",
            agent=agent_name,
            event_type=event_type,
            correlation_id=correlation_str,
            next_hop=next_hop,
            max_hops=MAX_HOPS,
        )
        _log_error(
            f"broker refused to spawn {agent_name} — chain depth {next_hop} >= MAX_HOPS={MAX_HOPS}",
            agent=agent_name,
            event_type=event_type,
            correlation_id=correlation_str,
            hop_count=next_hop,
        )
        return

    try:
        _write_handoff_message(
            from_agent=source_agent,
            to_agent=agent_name,
            event_type=event_type,
            run_id=run_id,
            correlation_id=correlation_str,
            hop_count=next_hop,
            meta={
                "event_id": event_id,
                "payload_keys": list(payload.keys()),
            },
        )
    except HandoffLogWriteFailed as exc:
        _log_error(
            f"broker refused to spawn {agent_name} on {event_type} — handoff log write failed",
            traceback=str(exc),
            agent=agent_name,
            event_type=event_type,
        )
        _enqueue_dead_letter(
            event_type=event_type, payload=payload, source_agent=source_agent,
            target_agent=agent_name, event_id=event_id, correlation_id=correlation_str,
            error=str(exc),
        )
        return  # skip this run; sweep will replay from the DLQ

    # 2. Create a queued run row in agent_runs
    try:
        run_lifecycle.create_run(
            run_id=run_id,
            agent_name=agent_name,
            source_channel="event",
            inputs={
                "event_type":    event_type,
                "event_payload": payload,
                "event_id":      event_id,
            },
            triggered_by=f"event:{event_type}",
        )
    except Exception as exc:
        log.error("broker_create_run_failed", agent=agent_name, event_type=event_type, error=str(exc))
        _log_error(
            f"broker create_run failed for {agent_name} on {event_type}",
            traceback=str(exc),
            agent=agent_name,
            event_type=event_type,
        )
        _enqueue_dead_letter(
            event_type=event_type, payload=payload, source_agent=source_agent,
            target_agent=agent_name, event_id=event_id, correlation_id=correlation_str,
            error=str(exc),
        )
        return

    # 3. Execute synchronously — this thread blocks until the agent completes or HITL-pauses
    try:
        execute_run(run_id)
        log.info("broker_trigger_completed", agent=agent_name, run_id=run_id)
    except Exception as exc:
        log.error("broker_execute_failed", agent=agent_name, run_id=run_id, error=str(exc))
        _log_error(
            f"broker execute_run failed for {agent_name}",
            traceback=str(exc),
            agent=agent_name,
            run_id=run_id,
        )
        _enqueue_dead_letter(
            event_type=event_type, payload=payload, source_agent=source_agent,
            target_agent=agent_name, event_id=event_id, correlation_id=correlation_str,
            error=str(exc),
        )


def _trigger_agent(event_row: dict[str, Any], agent_name: str) -> None:
    """Spawn a daemon thread to run _handle_event without blocking the Realtime callback."""
    t = threading.Thread(
        target=_handle_event,
        args=(event_row, agent_name),
        daemon=True,
        name=f"broker-{agent_name}-{event_row.get('type', 'event')[:20]}",
    )
    t.start()


def _dispatch(event_row: dict[str, Any]) -> None:
    """Router: called for every subscribed event INSERT. Fans out to downstream agents."""
    event_type = event_row.get("type", "")
    agents = EVENT_SUBSCRIPTIONS.get(event_type, [])
    if not agents:
        return
    log.info("broker_dispatch", event_type=event_type, agents=agents)
    for agent_name in agents:
        _trigger_agent(event_row, agent_name)


# ── Public startup function ───────────────────────────────────────────────────

def start_broker() -> Any:
    """Subscribe to all events in EVENT_SUBSCRIPTIONS.

    Returns the Supabase Realtime channel so the caller can unsubscribe on shutdown.
    Call once from main.py lifespan after start_scheduler().
    """
    all_event_types = list(EVENT_SUBSCRIPTIONS.keys())
    channel = subscribe(all_event_types, _dispatch)
    log.info(
        "event_broker_started",
        subscribed_to=all_event_types,
        agent_count=len({a for agents in EVENT_SUBSCRIPTIONS.values() for a in agents}),
    )
    return channel
