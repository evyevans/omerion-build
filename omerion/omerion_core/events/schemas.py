"""Typed Pydantic schemas for every event in EVENT_SUBSCRIPTIONS.

These are the **handoff contracts** of the system. Every event flowing from
one agent to another must match one of these models. The schemas are
consumed by:

  * `agent_wrapper.run()` — validates output before emission
  * `broker.emit_typed()` — validates at emit time (Wave 2)
  * `events.bus.emit_event()` — implementation will look up the schema by
    event_type and validate, so the 64 existing call sites get free contract
    enforcement without signature changes (Wave 2 §7).

Design notes:

  * Every event carries `correlation_id` so a business chain
    (account → contact → score → outreach → build → outcome) can be traced
    end-to-end.
  * Every event carries a `natural_key` property that defines what the
    idempotency utility hashes for dedupe. This is the *business identity*
    of the event — e.g., a `ContactScored` for a given (contact_id, scored_at_day)
    must only be processed once, even if the event is re-delivered.
  * `idempotency_key` is computed by the wrapper from `natural_key + time_window`
    — schemas declare *what* is unique, not *how* it's hashed.

The event types and string values mirror `omerion_core.events.bus.EventType`
exactly. Do not introduce a new event type here without also adding it to
the enum and EVENT_SUBSCRIPTIONS in broker.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from omerion_core.events.bus import EventType


# ─────────────────────────── base ─────────────────────────────────

class EventEnvelope(BaseModel):
    """Common envelope fields carried by every event.

    The wrapper guarantees `correlation_id` is propagated from the trigger
    through every downstream emission. `idempotency_key` is filled in by the
    wrapper using `omerion_core.util.idempotency.generate_key()`.
    """

    # NOTE: model-level strict=True is intentionally NOT set. Every emitter
    # serializes UUIDs/datetimes as strings (e.g. str(contact_id)), which strict
    # mode would reject. The fail-loud-on-bad-data guarantee is delivered by
    # extra="forbid" + the bus strict-schema gate (raises on validation failure)
    # + the DLQ poison-pill guard (parks invalid payloads). Where a NUMERIC/BOOL
    # field must never silently coerce (e.g. a score), use Strict() per-field.
    model_config = ConfigDict(extra="forbid", frozen=False)

    event_type: str
    source_agent: str = Field(min_length=1)
    correlation_id: UUID
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(min_length=8, max_length=128)
    run_id: UUID | None = None  # producer's run_id, for traceability

    @property
    def natural_key(self) -> str:
        """Subclasses override to return the business identity tuple as a string.

        The wrapper hashes this + a time-window bucket to produce
        `idempotency_key`. Default uses event_type + a fresh UUID — safe but
        means every emission is unique (no dedupe). Subclasses should
        override to enable proper idempotency.
        """
        return f"{self.event_type}:{uuid4()}"


# ─────────────────────────── 1. ACCOUNT_BATCH_READY ───────────────

class AccountBatchReady(EventEnvelope):
    """market-mapper → lead-scraper.

    A new batch of accounts has been discovered and is ready for contact
    enrichment.
    """
    event_type: Literal["account.batch.ready"] = "account.batch.ready"

    batch_id: UUID
    # market_mapper emits `count` (tools.emit_batch_ready_payload); `account_count`
    # is the historical name. Accept either — both optional so the round-trip of
    # the real emit payload (which sends `count`) succeeds.
    account_count: int | None = Field(default=None, ge=0)
    count: int | None = Field(default=None, ge=0)
    account_ids: list[UUID] = Field(default_factory=list)
    market_slug: str | None = None
    # Emitted by market_mapper/graph.py:97-98.
    market: str | None = None
    personas: list[str] = Field(default_factory=list)

    @property
    def natural_key(self) -> str:
        return f"account.batch.ready:{self.batch_id}"


# ─────────────────────────── 2. CONTACT_ENRICHED ──────────────────

class ContactEnriched(EventEnvelope):
    """lead-scraper → icp-scoring.

    A contact has been enriched with email, title, persona, etc. and is
    ready for scoring.
    """
    event_type: Literal["contact.enriched"] = "contact.enriched"

    contact_id: UUID
    account_id: UUID
    # lead_scraper_enricher/graph.py:241 does NOT send enriched_field_count;
    # relaxed to optional so the real emit payload round-trips.
    enriched_field_count: int | None = Field(default=None, ge=0)
    # Emitted by lead_scraper_enricher/graph.py:247.
    persona: str | None = None

    @property
    def natural_key(self) -> str:
        return f"contact.enriched:{self.contact_id}"


# ─────────────────────────── 3. CONTACT_SCORED ────────────────────

class ContactScored(EventEnvelope):
    """icp-scoring → (linkedin-outreach || crm-nurture || offer-matching).

    Fan-out trigger. The wrapper-level cohort planner is responsible for
    deciding which of the three downstream channels touches the contact
    (not all three simultaneously — see plan §dual-contact prevention).
    """
    event_type: Literal["contact.scored"] = "contact.scored"

    contact_id: UUID
    account_id: UUID
    # icp_scoring/graph.py:113 emits {contact_id, account_id, segment, final}.
    # The 0–100 scaled scores below are NOT produced by the current emitter, so
    # they are optional (bounds still enforced WHEN present — a negative score is
    # still rejected). `segment` + `final` (0.0–1.0) are what icp actually sends.
    # strict=True: a routing/decision score must NEVER silently coerce (e.g.
    # "80" -> 80, or True -> 1). Bad numeric data fails loud → DLQ, not a corrupt
    # score into the cohort planner. None and native int/float still accepted.
    fit_score: int | None = Field(default=None, ge=0, le=100, strict=True)
    intent_score: int | None = Field(default=None, ge=0, le=100, strict=True)
    timing_score: int | None = Field(default=None, ge=0, le=100, strict=True)
    total_score: int | None = Field(default=None, ge=0, le=300, strict=True)
    persona: str | None = None
    # floats stay lax: int->float (1 -> 1.0) is benign and can occur on JSONB
    # round-trip; strict float would reject it and poison-pill the DLQ replay.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    segment: str | None = None
    final: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def natural_key(self) -> str:
        # One score per contact per day — re-scoring tomorrow is allowed.
        day = self.emitted_at.date().isoformat()
        return f"contact.scored:{self.contact_id}:{day}"


# ─────────────────────────── 4. BLUEPRINT_APPROVED ────────────────

class BlueprintApproved(EventEnvelope):
    """meeting-intelligence → build-orchestrator (also emitted on /hitl/resolve).

    A discovery-call blueprint has been approved (by founder HITL) and is
    ready for the build pipeline.
    """
    event_type: Literal["blueprint.approved"] = "blueprint.approved"

    blueprint_id: UUID
    client_id: UUID | None = None
    # meeting_intelligence/graph.py:266 emits {blueprint_id, meeting_id} only —
    # approved_by/approval_id are not sent, so they are optional.
    approved_by: str | None = None  # "founder" | "hitl-auto" — never an agent name
    approval_id: UUID | None = None
    meeting_id: str | None = None

    @property
    def natural_key(self) -> str:
        # One approval per blueprint.
        return f"blueprint.approved:{self.blueprint_id}"


# ─────────────────────────── 5. RD_PROPOSAL_SUBMITTED ─────────────

class RdProposalSubmitted(EventEnvelope):
    """r3_strategic_architect → build-orchestrator.

    A research-driven improvement proposal has been submitted for execution.
    """
    event_type: Literal["rd.proposal.submitted"] = "rd.proposal.submitted"

    proposal_id: UUID
    # r3_strategic_architect/graph.py:221 emits {title, target_module, impact,
    # effort, priority_score, blueprint_handoff}. The original required fields
    # below are not sent, so they are optional; the actually-emitted fields are
    # added explicitly.
    priority: Literal["low", "medium", "high", "critical"] | None = None
    impact_summary: str | None = Field(default=None, max_length=2000)
    effort_estimate_hours: float | None = Field(default=None, ge=0)
    title: str | None = None
    target_module: str | None = None
    impact: str | None = None
    effort: str | None = None
    priority_score: float | None = None
    blueprint_handoff: bool | None = None

    @property
    def natural_key(self) -> str:
        return f"rd.proposal.submitted:{self.proposal_id}"


# ─────────────────────────── 6. DEPLOYMENT_LIVE ───────────────────

class DeploymentLive(EventEnvelope):
    """build-orchestrator → outcome-attribution.

    A client deployment is live; KPI attribution measurement starts now.
    """
    event_type: Literal["deployment.live"] = "deployment.live"

    deployment_id: UUID
    client_id: UUID  # REQUIRED — outcome-attribution/deployer consume it (event_ingress.py:151)
    blueprint_id: UUID | None = None
    # builder/graph.py:418 does not send deployed_at; relaxed to optional.
    deployed_at: datetime | None = None
    # Additional fields builder emits.
    status: str | None = None
    merged: int | None = None
    ready: int | None = None
    prs: list[str] = Field(default_factory=list)

    @property
    def natural_key(self) -> str:
        return f"deployment.live:{self.deployment_id}"


# ──────────── 7. DEPLOYMENT_HEALTH_CONFIRMED / HEALTH_FAILED ──────

class DeploymentHealthConfirmed(EventEnvelope):
    """deployer → outcome-attribution.

    Smoke tests passed; the live endpoint returned HTTP 200 within 60 s.
    """
    event_type: Literal["deployment.health_confirmed"] = "deployment.health_confirmed"

    deployment_id: UUID
    client_id: UUID
    health_url: str
    smoke_status_code: int
    backup_ref: str | None = None

    @property
    def natural_key(self) -> str:
        return f"deployment.health_confirmed:{self.deployment_id}"


class DeploymentHealthFailed(EventEnvelope):
    """deployer → founder-hitl / healer.

    Smoke tests failed or timed out. May include rollback outcome.
    failure_reason values: migration_error | provision_error | smoke_timeout | rollback_failed
    """
    event_type: Literal["deployment.health_failed"] = "deployment.health_failed"

    deployment_id: UUID
    client_id: UUID
    failure_reason: str
    rollback_ok: bool | None = None
    backup_ref: str | None = None

    @property
    def natural_key(self) -> str:
        return f"deployment.health_failed:{self.deployment_id}"


# ─────────────────────────── 8. ATTRIBUTION_REPORT_READY ──────────

class AttributionReportReady(EventEnvelope):
    """outcome-attribution → strategic-arch.

    Pre/post KPI delta has been computed; strategy can refine.
    """
    event_type: Literal["attribution.report.ready"] = "attribution.report.ready"

    # outcome_attribution/graph.py:173 may emit report_id/client_id as None
    # (str(x) if x else None) and does NOT send period_days/delta_pct/
    # delta_confidence — all relaxed to optional. The proof fields it DOES send
    # are added explicitly.
    report_id: UUID | None = None
    deployment_id: UUID
    client_id: UUID | None = None
    period_days: int | None = Field(default=None, ge=1)
    delta_pct: float | None = None
    delta_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    proof_point: str | None = None
    significant_count: int | None = None
    revenue_delta_usd: float | None = None

    @property
    def natural_key(self) -> str:
        return f"attribution.report.ready:{self.report_id}"


# ─────────────────────────── 8. RD_INSIGHT_CREATED ────────────────

class RdInsightCreated(EventEnvelope):
    """r1_market_tech_watcher → (oss-scout || strategic-arch).

    One tagged market/tech insight has been written. Triggers OSS scout
    on relevance + R3 strategic synthesis.
    """
    event_type: Literal["rd.insight.created"] = "rd.insight.created"

    insight_id: UUID
    # r1_market_tech_watcher/graph.py:99 emits {run_date, title, impact_tag,
    # estimated_priority, source_url, source_type}. `kind`/`priority_tag` are
    # not sent (relaxed); the actually-emitted fields are added.
    kind: str | None = None  # e.g., "framework_release", "funding", "model_launch"
    priority_tag: Literal["low", "medium", "high"] | None = None
    source_url: str
    title: str = Field(min_length=1, max_length=500)
    run_date: str | None = None
    impact_tag: str | None = None
    estimated_priority: str | None = None
    source_type: str | None = None

    @property
    def natural_key(self) -> str:
        return f"rd.insight.created:{self.insight_id}"


# ─────────────────────────── 9. OSS_CANDIDATE_SCORED ──────────────

class OssCandidateScored(EventEnvelope):
    """r2_oss_scout → strategic-arch.

    One OSS repo has been scored on stars, recency, and fit.
    """
    event_type: Literal["rd.oss_candidate.scored"] = "rd.oss_candidate.scored"

    candidate_id: UUID
    repo_url: str
    # r2_oss_scout/graph.py:76 emits {run_date, name, impact_tag,
    # integration_type, fit, risk, overall}. `score`/`stars`/`kind` are not sent
    # (relaxed); the actually-emitted fields are added.
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    stars: int | None = Field(default=None, ge=0)
    kind: str | None = None  # "framework" | "library" | "tool" | "model"
    run_date: str | None = None
    name: str | None = None
    impact_tag: str | None = None
    integration_type: str | None = None
    fit: float | None = None
    risk: float | None = None
    overall: float | None = None

    @property
    def natural_key(self) -> str:
        return f"rd.oss_candidate.scored:{self.candidate_id}"


# ─────────────────────────── 10. ANALYSIS_READY ───────────────────

class AnalysisReady(EventEnvelope):
    """r2_oss_scout (batch heartbeat) → strategic-arch.

    Observability batch event — emitted once per OSS scout sweep so
    Mission Control can chart sweep cadence.
    """
    event_type: Literal["analysis.ready"] = "analysis.ready"

    # r2_oss_scout/graph.py:92 emits {run_date, kind, inserted, top_overall}.
    # The original batch-id/sweep fields are not sent (relaxed); the
    # actually-emitted fields are added.
    batch_id: UUID | None = None
    candidates_count: int | None = Field(default=None, ge=0)
    sweep_started_at: datetime | None = None
    sweep_finished_at: datetime | None = None
    run_date: str | None = None
    kind: str | None = None
    inserted: int | None = None
    top_overall: float | None = None

    @property
    def natural_key(self) -> str:
        return f"analysis.ready:{self.batch_id or self.run_date}"


# ─────────────────────────── 11. DOSSIER_READY ────────────────────

class DossierReady(EventEnvelope):
    """high_quality_lead_scraping → strategic-arch.

    A multi-source research dossier has been written for one account.
    """
    event_type: Literal["dossier.ready"] = "dossier.ready"

    dossier_id: UUID
    account_id: UUID
    # high_quality_lead_scraping/graph.py:303 emits {confidence, offer_match};
    # completeness_score is not sent (relaxed).
    completeness_score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    offer_match: str | None = None

    @property
    def natural_key(self) -> str:
        return f"dossier.ready:{self.dossier_id}"


# ─────────────────────────── 12. PROPOSAL_DRAFT_READY ─────────────

class ProposalDraftReady(EventEnvelope):
    """offer_matching → build-orchestrator.

    A high-confidence proposal draft is ready for the build pipeline.
    The `value_bucket` is the Wave-2 deterministic bucket (S/M/L/XL) — no
    raw LLM-driven dollar amount survives to opportunities.value_est_usd.
    """
    event_type: Literal["proposal.draft.ready"] = "proposal.draft.ready"

    # offer_matching/graph.py:150 emits a BATCH summary
    # {opportunity_ids, high_confidence_count, max_confidence, stats} — NOT the
    # per-proposal fields below. All originals relaxed to optional; the
    # actually-emitted fields are added.
    proposal_id: UUID | None = None
    contact_id: UUID | None = None
    account_id: UUID | None = None
    value_bucket: Literal["S", "M", "L", "XL"] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    opportunity_ids: list[UUID] = Field(default_factory=list)
    high_confidence_count: int | None = None
    max_confidence: float | None = None
    stats: dict | None = None

    @property
    def natural_key(self) -> str:
        return f"proposal.draft.ready:{self.proposal_id}"


# ─────────────────── 13. BUILD_TASK_CREATED ───────────────────────────────────

class BuildTaskCreated(EventEnvelope):
    """build-orchestrator → builder.

    Emitted after blueprint decomposition. Carries the full task list so
    BUILDER can load each TaskSpec from the build_tasks table by slug.
    """
    event_type: Literal["build.task.created"] = "build.task.created"

    blueprint_id: UUID
    deployment_id: UUID | None = None
    task_count: int = Field(ge=0)
    tasks: list[dict] = Field(default_factory=list)  # [{slug, phase, title}]

    @property
    def natural_key(self) -> str:
        return f"build.task.created:{self.blueprint_id}"


# ─────────────────── 14. BUILD_TASK_COMPLETED ─────────────────────────────────

class BuildTaskCompleted(EventEnvelope):
    """builder → outcome-attribution / monitoring.

    Emitted once per task that BUILDER successfully codes, tests, and PRs.
    """
    event_type: Literal["build.task.completed"] = "build.task.completed"

    deployment_id: UUID
    task_slug: str
    pr_url: str
    pr_number: int
    attempts: int = 1  # how many write→test cycles it took

    @property
    def natural_key(self) -> str:
        return f"build.task.completed:{self.deployment_id}:{self.task_slug}"


# ─────────────────── 14. BUILD_TASK_FAILED ────────────────────────────────────

class BuildTaskFailed(EventEnvelope):
    """builder → monitoring / HITL escalation.

    Emitted when BUILDER exhausts all retries and cannot produce passing tests.
    """
    event_type: Literal["build.task.failed"] = "build.task.failed"

    deployment_id: UUID
    task_slug: str
    failure_reason: str
    attempts: int = 3

    @property
    def natural_key(self) -> str:
        return f"build.task.failed:{self.deployment_id}:{self.task_slug}"


# ─────────────────────────── 15. REGRESSION_ALERT ───────────────

class RegressionAlert(EventEnvelope):
    """r4_regression_alert (script) → healer.

    Emitted when a rolling-window metric for an agent crosses a threshold.
    alert_run_id traces back to the agent_runs row that triggered R4.
    """
    event_type: Literal["regression.alert"] = "regression.alert"

    failing_agent: str = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    metric: str = Field(min_length=1)       # e.g. "error_rate", "latency_ms", "cost_usd"
    metric_value: float
    alert_run_id: str | None = None         # run_id that crossed the threshold

    @property
    def natural_key(self) -> str:
        day = self.emitted_at.date().isoformat()
        return f"regression.alert:{self.failing_agent}:{self.metric}:{day}"


# ─────────────────────────── 16. HEALING_APPLIED ─────────────────

class HealingApplied(EventEnvelope):
    """healer → auditor.

    Emitted after HEALER applies (or skips) a fix. AUDITOR uses this as an
    event-mode trigger so it scans the audit_log row immediately rather than
    waiting for the nightly cron.
    """
    event_type: Literal["healing.applied"] = "healing.applied"

    healed_agent: str = Field(min_length=1)
    remediation_type: str                   # "config_patch" | "prompt_update" | "escalated"
    audit_id: str | None = None             # UUID of the audit_log row HEALER wrote
    fix_applied: bool

    @property
    def natural_key(self) -> str:
        day = self.emitted_at.date().isoformat()
        return f"healing.applied:{self.audit_id or self.healed_agent}:{day}"


# ─────────────────────────── 20. CLIENT_PROFILE_READY ────────────
class ClientProfileReady(EventEnvelope):
    """client_intake → meeting-intelligence.

    A client profile has been extracted and persisted. Downstream agents
    (meeting-intelligence) use this to pre-populate session context.
    """
    event_type: Literal["client.profile.ready"] = "client.profile.ready"

    blueprint_id: str
    client_id: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    # factory_intake/graph.py:142 adds `source` ("self_serve") to distinguish
    # from meeting-transcript intake.
    source: str | None = None

    @property
    def natural_key(self) -> str:
        return f"client.profile.ready:{self.blueprint_id}:{self.client_id}"


# ─────────────────────────── 21. CLIENT_INTAKE_GAPS_DETECTED ─────
class ClientIntakeGapsDetected(EventEnvelope):
    """client_intake → founder (Discord alert via HITL).

    Confidence < 0.7 and at least one CRITICAL gap remain after extraction.
    No auto-consumer — surfaces to founder for follow-up.
    """
    event_type: Literal["client.intake.gaps_detected"] = "client.intake.gaps_detected"

    blueprint_id: str
    client_id: str
    data_gaps: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)

    @property
    def natural_key(self) -> str:
        return f"client.intake.gaps_detected:{self.blueprint_id}:{self.client_id}"


# ─────────── 22. CLIENT_ONBOARDED ─────────────────────────────────
class ClientOnboarded(EventEnvelope):
    """client_onboarding → compliance-checker.

    A new client has completed onboarding (kickoff sent, reporting scheduled).
    """
    event_type: Literal["client.onboarded"] = "client.onboarded"

    client_id: str
    client_name: str | None = None
    kickoff_sent: bool | None = None
    reporting_scheduled: bool | None = None

    @property
    def natural_key(self) -> str:
        return f"client.onboarded:{self.client_id}"


# ─────────── 23. AUTOMATION_BLUEPRINT_APPROVED ────────────────────
class AutomationBlueprintApproved(EventEnvelope):
    """automation_strategist → spec-architect / executive-polisher.

    A founder-approved automation blueprint is ready for decomposition + polish.
    """
    event_type: Literal["automation.blueprint.approved"] = "automation.blueprint.approved"

    blueprint_id: str
    client_id: str | None = None
    use_case_count: int | None = None

    @property
    def natural_key(self) -> str:
        return f"automation.blueprint.approved:{self.blueprint_id}"


# ─────────── 24. AUTOMATION_BLUEPRINT_POLISHED ────────────────────
class AutomationBlueprintPolished(EventEnvelope):
    """executive_polisher → diagram-delivery.

    The client-facing artifact for an approved automation blueprint is polished.
    """
    event_type: Literal["automation.blueprint.polished"] = "automation.blueprint.polished"

    blueprint_id: str
    client_id: str | None = None

    @property
    def natural_key(self) -> str:
        return f"automation.blueprint.polished:{self.blueprint_id}"


# ─────────── 25. BLUEPRINT_DRAFT_CREATED ──────────────────────────
class BlueprintDraftCreated(EventEnvelope):
    """meeting_intelligence → client-intake.

    A draft blueprint has been created from a discovery-call transcript.
    """
    event_type: Literal["blueprint.draft.created"] = "blueprint.draft.created"

    blueprint_id: str
    meeting_id: str | None = None

    @property
    def natural_key(self) -> str:
        return f"blueprint.draft.created:{self.blueprint_id}"


# ─────────── 26. PROMPT_UPDATE_APPLIED ────────────────────────────
class PromptUpdateApplied(EventEnvelope):
    """trainer → auditor.

    TRAINER applied a founder-approved prompt improvement to prompts.py.
    AUDITOR verifies the self-modification (Rule 3 HITL_BYPASS).
    """
    event_type: Literal["prompt.update.applied"] = "prompt.update.applied"

    iso_week: str
    applied_count: int | None = None
    skipped_count: int | None = None
    targets: list[dict] = Field(default_factory=list)  # [{agent, prompt}]
    decision: str | None = None

    @property
    def natural_key(self) -> str:
        return f"prompt.update.applied:{self.iso_week}"


# ─────────── 27. FACTORY_PAYMENT_CONFIRMED ────────────────────────
class FactoryPaymentConfirmed(EventEnvelope):
    """stripe webhook → factory-intake.

    A Stripe checkout completed for a factory blueprint; intake can start.
    """
    event_type: Literal["factory.payment.confirmed"] = "factory.payment.confirmed"

    session_id: str
    blueprint_id: str | None = None
    amount_usd: float | None = None
    stripe_event_id: str | None = None

    @property
    def natural_key(self) -> str:
        return f"factory.payment.confirmed:{self.stripe_event_id or self.session_id}"


# ─────────── 28. PROPOSAL_ACCEPTED ────────────────────────────────
class ProposalAccepted(EventEnvelope):
    """(external/manual) → client-onboarding.

    A client accepted a proposal. No in-repo emitter yet — manual/external
    trigger; minimal contract so the subscribed event has a schema.
    """
    event_type: Literal["proposal.accepted"] = "proposal.accepted"

    proposal_id: str
    client_id: str | None = None

    @property
    def natural_key(self) -> str:
        return f"proposal.accepted:{self.proposal_id}"


# ─────────────────────────── registry ─────────────────────────────

# Maps EventType.value → schema class. `bus.emit_event()` looks up the schema
# by event_type and validates the payload against it (Wave 2). When adding a
# new event, register it here AND add it to broker.EVENT_SUBSCRIPTIONS if it
# has downstream consumers.
EVENT_SCHEMAS: dict[str, type[EventEnvelope]] = {
    EventType.ACCOUNT_BATCH_READY.value:      AccountBatchReady,
    EventType.CONTACT_ENRICHED.value:         ContactEnriched,
    EventType.CONTACT_SCORED.value:           ContactScored,
    EventType.BLUEPRINT_APPROVED.value:       BlueprintApproved,
    EventType.RD_PROPOSAL_SUBMITTED.value:    RdProposalSubmitted,
    EventType.DEPLOYMENT_LIVE.value:          DeploymentLive,
    EventType.ATTRIBUTION_REPORT_READY.value: AttributionReportReady,
    EventType.RD_INSIGHT_CREATED.value:       RdInsightCreated,
    EventType.OSS_CANDIDATE_SCORED.value:     OssCandidateScored,
    EventType.ANALYSIS_READY.value:           AnalysisReady,
    EventType.DOSSIER_READY.value:            DossierReady,
    EventType.PROPOSAL_DRAFT_READY.value:     ProposalDraftReady,
    EventType.BUILD_TASK_CREATED.value:             BuildTaskCreated,
    EventType.BUILD_TASK_COMPLETED.value:           BuildTaskCompleted,
    EventType.BUILD_TASK_FAILED.value:              BuildTaskFailed,
    EventType.DEPLOYMENT_HEALTH_CONFIRMED.value:    DeploymentHealthConfirmed,
    EventType.DEPLOYMENT_HEALTH_FAILED.value:       DeploymentHealthFailed,
    EventType.REGRESSION_ALERT.value:               RegressionAlert,
    EventType.HEALING_APPLIED.value:                HealingApplied,
    EventType.CLIENT_PROFILE_READY.value:           ClientProfileReady,
    EventType.CLIENT_INTAKE_GAPS_DETECTED.value:    ClientIntakeGapsDetected,
    # Previously subscribed but unschema'd — working via permissive pass-through.
    EventType.CLIENT_ONBOARDED.value:               ClientOnboarded,
    EventType.AUTOMATION_BLUEPRINT_APPROVED.value:  AutomationBlueprintApproved,
    EventType.BLUEPRINT_DRAFT_CREATED.value:        BlueprintDraftCreated,
    EventType.PROMPT_UPDATE_APPLIED.value:          PromptUpdateApplied,
    EventType.FACTORY_PAYMENT_CONFIRMED.value:      FactoryPaymentConfirmed,
    # Subscribed via raw strings (not EventType enum members).
    "automation.blueprint.polished":                AutomationBlueprintPolished,
    "proposal.accepted":                            ProposalAccepted,
}


# Discriminated union for callers that want to parse a generic event row into
# the correct subtype. Pydantic chooses the right schema based on event_type.
TypedEvent = Annotated[
    Union[
        AccountBatchReady,
        ContactEnriched,
        ContactScored,
        BlueprintApproved,
        RdProposalSubmitted,
        DeploymentLive,
        AttributionReportReady,
        RdInsightCreated,
        OssCandidateScored,
        AnalysisReady,
        DossierReady,
        ProposalDraftReady,
        BuildTaskCreated,
        BuildTaskCompleted,
        BuildTaskFailed,
        DeploymentHealthConfirmed,
        DeploymentHealthFailed,
        RegressionAlert,
        HealingApplied,
    ],
    Field(discriminator="event_type"),
]


def parse_event(event_type: str, payload: dict[str, Any]) -> EventEnvelope:
    """Look up a schema by event_type and validate the payload.

    Raises `KeyError` if event_type is not registered; raises pydantic
    `ValidationError` if the payload does not match.
    """
    schema = EVENT_SCHEMAS[event_type]
    # Ensure event_type matches what the schema expects (defensive — the
    # Literal[...] constraint already enforces this at model_validate time).
    merged = {"event_type": event_type, **payload}
    return schema.model_validate(merged)


__all__ = [
    "EventEnvelope",
    "AccountBatchReady",
    "ContactEnriched",
    "ContactScored",
    "BlueprintApproved",
    "RdProposalSubmitted",
    "DeploymentLive",
    "AttributionReportReady",
    "RdInsightCreated",
    "OssCandidateScored",
    "AnalysisReady",
    "DossierReady",
    "ProposalDraftReady",
    "BuildTaskCreated",
    "BuildTaskCompleted",
    "BuildTaskFailed",
    "RegressionAlert",
    "HealingApplied",
    "ClientProfileReady",
    "ClientIntakeGapsDetected",
    "ClientOnboarded",
    "AutomationBlueprintApproved",
    "AutomationBlueprintPolished",
    "BlueprintDraftCreated",
    "PromptUpdateApplied",
    "FactoryPaymentConfirmed",
    "ProposalAccepted",
    "EVENT_SCHEMAS",
    "TypedEvent",
    "parse_event",
]
