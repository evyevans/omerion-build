"""Canonical event bus — thin wrapper over Supabase `events` table.

Every agent emits via `emit_event(...)`. Downstream agents either
listen via Supabase Realtime or poll the table.

Event type vocabulary comes from the master blueprint §9.2.

**Wave 2.7 — typed validation inside the existing signature:**
The 64 agent call sites continue to call `emit_event(event_type, source_agent, payload)`
unchanged. Internally, if the event_type is registered in
`events.schemas.EVENT_SCHEMAS`, the payload is validated against the
schema before persistence. Validation failure raises (loud) rather than
silently emitting a malformed event downstream.

Set `OMERION_STRICT_EVENT_SCHEMAS=0` to log-and-emit on validation error
instead of raising (useful during the per-agent Wave 1.9 migration when
some agents still emit the legacy unstructured shape).
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any, Callable
from uuid import UUID

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.events")


class EventType(str, Enum):
    # ─── Account / market ────────────────────────────────────
    ACCOUNT_DISCOVERED   = "account.discovered"
    ACCOUNT_UPDATED      = "account.updated"
    ACCOUNT_BATCH_READY  = "account.batch.ready"

    # ─── Contact / enrichment ───────────────────────────────
    CONTACT_ENRICHED     = "contact.enriched"
    CONTACT_SCORED       = "contact.scored"
    CONTACT_COHORT_READY = "contact.cohort.ready"

    # ─── Outreach ───────────────────────────────────────────
    OUTREACH_LI_SENT     = "outreach.linkedin.sent"
    OUTREACH_EMAIL_SENT  = "outreach.email.sent"
    OUTREACH_SMS_SENT    = "outreach.sms.sent"
    OUTREACH_REPLIED     = "outreach.replied"

    # ─── Discovery / blueprint ──────────────────────────────
    MEETING_TRANSCRIPT_RECEIVED = "meeting.transcript.received"
    BLUEPRINT_DRAFT_CREATED     = "blueprint.draft.created"
    BLUEPRINT_APPROVED          = "blueprint.approved"
    BLUEPRINT_REJECTED          = "blueprint.rejected"

    # ─── Build / deploy ─────────────────────────────────────
    BUILD_TASK_CREATED   = "build.task.created"
    BUILD_TASK_COMPLETED = "build.task.completed"
    BUILD_TASK_FAILED    = "build.task.failed"
    DEPLOYMENT_LIVE               = "deployment.live"
    DEPLOYMENT_FAILED             = "deployment.failed"
    DEPLOYMENT_HEALTH_CONFIRMED   = "deployment.health_confirmed"
    DEPLOYMENT_HEALTH_FAILED      = "deployment.health_failed"

    # ─── Attribution / R&D ──────────────────────────────────
    ATTRIBUTION_REPORT_READY = "attribution.report.ready"
    RD_PROPOSAL_SUBMITTED    = "rd.proposal.submitted"
    RD_INSIGHTS_BATCH_READY  = "rd.insights.batch.ready"
    ANALYSIS_READY           = "analysis.ready"        # R2 OSS Scout: oss_projects batch written

    # ─── Research / dossiers ────────────────────────────────
    DOSSIER_CREATED      = "dossier.created"
    DOSSIER_READY        = "dossier.ready"             # Analyst: research dossier written

    # ─── Reporting ──────────────────────────────────────────
    FOUNDER_DAILY_DIGEST = "founder.daily_digest"

    # ─── Telemetry ──────────────────────────────────────────
    REGRESSION_ALERT       = "regression.alert"
    HEALING_APPLIED        = "healing.applied"
    PROPOSAL_READY         = "proposal.ready"
    PROPOSAL_DRAFT_READY   = "proposal.draft.ready"   # Offer Matching: confidence > threshold

    # ─── Audit (AUDITOR — constitutional guardian) ──────────
    # Dedicated events so audit violations no longer overload REGRESSION_ALERT
    # (which the HEALER consumes — a critical violation must NOT re-trigger the
    # healer). No auto-consumer: routed to the founder via Discord + dashboard.
    AUDIT_VIOLATION_DETECTED = "audit.violation.detected"
    AUDIT_SWEEP_COMPLETE     = "audit.sweep.complete"

    # ─── RSI prompt training (TRAINER → AUDITOR) ────────────
    # Emitted when TRAINER applies a founder-approved prompt improvement to
    # prompts.py. AUDITOR consumes it to verify the self-modification (Rule 3
    # HITL_BYPASS — the founder approval must exist). Closes the RSI loop.
    PROMPT_UPDATE_APPLIED    = "prompt.update.applied"

    # ─── HITL ───────────────────────────────────────────────
    HITL_APPROVED        = "hitl.approved"
    HITL_REJECTED        = "hitl.rejected"

    # ─── SEEK / Job Hunter ──────────────────────────────────
    JOB_POSTING_DISCOVERED = "job.posting.discovered"
    APPLICATION_DRAFTED    = "job.application.drafted"
    APPLICATION_SENT       = "job.application.sent"
    APPLICATION_RESPONDED  = "job.application.responded"
    APPLICATION_GHOSTED    = "job.application.ghosted"

    # ─── Outreach signals (RAG Traction) ────────────────────
    OUTREACH_GHOSTED        = "outreach.ghosted"
    OUTREACH_REENGAGED      = "outreach.reengaged"
    OUTREACH_THREAD_CREATED = "outreach.thread.created"
    OUTREACH_SIGNAL_INDEXED = "outreach.signal.indexed"

    # ─── Client lifecycle (Phase 5 — agency operations) ─────
    CLIENT_ONBOARDED            = "client.onboarded"
    CLIENT_ONBOARDING_REJECTED  = "client.onboarding.rejected"
    CLIENT_HEALTH_CHECKED       = "client.health.checked"
    CLIENT_CHURN_RISK           = "client.churn.risk"
    COMPETITOR_SIGNAL           = "competitor.signal.indexed"
    CLIENT_COMMS_SENT           = "client.comms.sent"

    # ─── Client intake ──────────────────────────────────────
    CLIENT_PROFILE_READY        = "client.profile.ready"
    CLIENT_INTAKE_GAPS_DETECTED = "client.intake.gaps_detected"

    # ─── R&D per-item granular events (replaces RD_INSIGHTS_BATCH_READY) ─
    OSS_CANDIDATE_SCORED   = "rd.oss_candidate.scored"
    RD_INSIGHT_CREATED     = "rd.insight.created"
    
    # ─── Factory ──────────────────────────────────────────────
    FACTORY_PAYMENT_CONFIRMED = "factory.payment.confirmed"
    # Agentic Factory chain (automation_strategist → spec_architect; factory_rag).
    # Previously emitted as raw strings, which bypassed schema validation.
    AUTOMATION_BLUEPRINT_APPROVED = "automation.blueprint.approved"
    AUTOMATION_BLUEPRINT_REJECTED = "automation.blueprint.rejected"
    AUTOMATION_DIAGRAM_DELIVERED = "automation.diagram.delivered"  # diagram_delivery terminal event
    FACTORY_PLAYBOOK_UPDATED = "factory.playbook.updated"

    # ─── Validator / PR review ──────────────────────────────────
    PR_VALIDATION_APPROVED = "pr.validation.approved"
    PR_VALIDATION_REJECTED = "pr.validation.rejected"

    # ─── QA Testing ─────────────────────────────────────────────
    QA_TESTS_PASSED             = "qa.tests.passed"
    QA_TESTS_FAILED             = "qa.tests.failed"

    # ─── Compliance ─────────────────────────────────────────────
    COMPLIANCE_SWEEP_COMPLETE     = "compliance.sweep.complete"
    COMPLIANCE_VIOLATION_DETECTED = "compliance.violation.detected"

    # ─── Security ───────────────────────────────────────────────
    SECURITY_SCAN_PASSED        = "security.scan.passed"
    SECURITY_VIOLATION_DETECTED = "security.violation.detected"


def _strict_schemas_enabled() -> bool:
    """Toggle whether schema validation failure raises (default) or logs."""
    return os.getenv("OMERION_STRICT_EVENT_SCHEMAS", "1") != "0"


def _validate_against_schema(
    event_type: str,
    source_agent: str,
    correlation_id: UUID | str | None,
    payload: dict[str, Any]
) -> None:
    """Wave 2.7: validate payload against `events.schemas.EVENT_SCHEMAS`.

    Imported lazily so that schemas.py can keep importing from bus.py
    (EventType enum) without a circular import. If the event_type isn't
    registered in EVENT_SCHEMAS, we let it through — schemas are added
    incrementally as agents migrate.
    """
    try:
        from omerion_core.events.schemas import EVENT_SCHEMAS
    except Exception:  # noqa: BLE001 — schemas optional during migration
        return

    schema_cls = EVENT_SCHEMAS.get(event_type)
    if schema_cls is None:
        return  # event has no schema yet — permissive pass-through

    import uuid
    try:
        schema_cls.model_validate({
            "event_type": event_type,
            "source_agent": source_agent,
            "correlation_id": correlation_id or uuid.uuid4(),
            "idempotency_key": f"dummy-{uuid.uuid4()}",
            **payload
        })
    except Exception as exc:  # noqa: BLE001
        if _strict_schemas_enabled():
            log.error(
                "event_schema_validation_failed_strict",
                event_type=event_type,
                error=str(exc),
            )
            raise ValueError(
                f"event {event_type!r} payload failed schema validation: {exc}"
            ) from exc
        log.warning(
            "event_schema_validation_failed_loose",
            event_type=event_type,
            error=str(exc),
        )


def emit_event(
    event_type: EventType | str,
    source_agent: str,
    payload: dict[str, Any] | None = None,
    *,
    contact_id: UUID | str | None = None,
    account_id: UUID | str | None = None,
    correlation_id: UUID | str | None = None,
) -> str:
    """Write an event to the bus. Returns the new event_id.

    Wave 2.7: if `event_type` has a Pydantic schema registered in
    `events.schemas.EVENT_SCHEMAS`, the payload is validated before
    persistence. The 64 existing call sites continue to work without
    changes — they get free contract enforcement.
    """
    et = event_type.value if isinstance(event_type, EventType) else event_type

    # Schema gate (Wave 2.7) — runs BEFORE the DB write. A schema-invalid
    # event never reaches the broker subscribers.
    _validate_against_schema(et, source_agent, correlation_id, payload or {})

    result = supabase.rpc("emit_event", {
        "p_type": et,
        "p_source_agent": source_agent,
        "p_payload": payload or {},
        "p_contact_id": str(contact_id) if contact_id else None,
        "p_account_id": str(account_id) if account_id else None,
        "p_correlation_id": str(correlation_id) if correlation_id else None,
    }).execute()
    event_id = result.data
    log.info("event_emitted", type=et, agent=source_agent, event_id=event_id)
    return event_id


RECONNECT_MAX_BACKOFF_SECONDS = 60.0


async def _connect_and_listen(ets: list[str], handler: Callable[[dict[str, Any]], None]) -> None:
    """One realtime connection attempt. Raises on connect/socket failure.

    Establishes the channel, subscribes to every event type, then keeps the
    connection alive. If the underlying websocket drops, the supabase realtime
    client surfaces it as an exception which propagates to the supervisor.
    """
    import asyncio

    from supabase import create_async_client
    from omerion_core.settings import settings

    client = await create_async_client(settings.supabase_url, settings.supabase_service_role_key)
    channel = client.channel("omerion-events")
    for et in ets:
        channel = channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table="events",
            filter=f"type=eq.{et}",
            callback=lambda payload: handler(payload["new"]),
        )
    await channel.subscribe()
    log.info("realtime_subscription_active", events_count=len(ets))
    # Keep the loop alive in short slices so a CancelledError (shutdown) or a
    # raised socket error propagates promptly to the supervisor for reconnect.
    while True:
        await asyncio.sleep(30)


def _alert_realtime_down(error: str) -> None:
    """Page the operator — a realtime drop means the event fleet stopped triggering.

    Best-effort; never raises.
    """
    try:
        import os

        import httpx
        from omerion_core.settings import settings

        url = (
            os.getenv("DISCORD_MISSION_CONTROL_WEBHOOK_URL")
            or getattr(settings, "discord_alerts_webhook_url", "")
            or ""
        )
        if not url:
            return
        with httpx.Client(timeout=5) as c:
            c.post(url, json={"content": f"🔌 OMERION realtime event subscription dropped — reconnecting. {error[:1500]}"})
    except Exception as exc:  # noqa: BLE001
        log.warning("realtime_alert_failed", error=str(exc))


async def _realtime_supervisor(
    ets: list[str],
    handler: Callable[[dict[str, Any]], None],
    *,
    max_backoff: float = RECONNECT_MAX_BACKOFF_SECONDS,
) -> None:
    """Supervise the realtime subscription: reconnect with backoff on any drop.

    The legacy code connected once and, on ANY disconnect, logged a single line
    and let the daemon thread die — silently darkening EVERY event-driven agent
    with no alert. This loop reconnects with exponential backoff and pages the
    operator on each crash, so a transient Supabase/Railway blip self-heals.
    """
    import asyncio

    backoff = 1.0
    while True:
        try:
            await _connect_and_listen(ets, handler)
            backoff = 1.0  # a clean return is unexpected → reconnect immediately
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("realtime_subscription_crashed", error=str(e))
            _alert_realtime_down(str(e))
            await asyncio.sleep(min(backoff, max_backoff))
            backoff = min(backoff * 2, max_backoff)


def subscribe(event_types: list[EventType | str], handler: Callable[[dict[str, Any]], None]) -> Any:
    """Subscribe to one or more event types via Supabase Realtime.

    Returns the daemon thread running the supervised async loop. The supervisor
    reconnects on any drop (see `_realtime_supervisor`), so the event fleet no
    longer goes permanently dark on a transient disconnect.
    """
    import asyncio
    import threading

    ets = [e.value if isinstance(e, EventType) else e for e in event_types]

    def _run_realtime() -> None:
        asyncio.run(_realtime_supervisor(ets, handler))

    t = threading.Thread(target=_run_realtime, daemon=True, name="supabase-realtime-bus")
    t.start()
    return t
