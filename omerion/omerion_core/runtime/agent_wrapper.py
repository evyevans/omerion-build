"""The single trust boundary — every agent invocation passes through here.

This is the wrapper Wave 1 was designed around. The architectural intent
(see /Users/evy/.claude/plans/purring-sauteeing-noodle.md):

    AI may propose. Deterministic code validates and commits.
    Every agent call passes through `agent_wrapper.run()`.
    There is no other path.

Pipeline (5 stages, all deterministic except stage 3):

    1. INPUT — validate caller-supplied input against the registered
                AgentInput schema (or a permissive default).
    2. PRE-AI  — idempotency dedupe; mutex per (skill, entity);
                 cohort optout filter; cost budget check;
                 run_lifecycle.create_run() with idempotency_key.
    3. AI      — delegate to run_executor.execute_run() which handles
                 wall-clock timeout, ThreadPool isolation, kill switch,
                 lifecycle transitions, cost rollup, and HITL detection.
    4. POST-AI — parse final state into AgentOutput; run style_guard.filter
                 on any human-facing drafts; verify recipient IDs are in
                 the filtered cohort (LLM cannot invent contacts); value
                 bounds check (Wave 2 — opt-in per agent); confidence
                 threshold → HITL routing.
    5. EMIT    — broker.emit_typed() if the output declares an event.

Per-agent migration (Wave 1.9) registers tighter contracts via
`register_contract(skill, input_model, output_model)`. Until an agent is
migrated, it runs with the permissive defaults (input passes through,
output is not strictly schema-checked, style_guard runs on any field
named in `text_fields`). This lets us migrate 15 agents incrementally
without breaking any single agent's existing behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.optout import is_opted_out
from omerion_core.runtime import run_lifecycle
from omerion_core.runtime.mutex import default_holder_id, mutex
from omerion_core.runtime.run_executor import execute_run
from omerion_core.settings import settings
from omerion_core.util.idempotency import generate_run_key

log = get_logger("omerion.agent_wrapper")


# ─────────────────────────── contracts ────────────────────────────

class AgentInput(BaseModel):
    """Base input contract every wrapped agent receives.

    Per-agent subclasses (defined in `omerion/agents/<name>/contracts.py`
    in Wave 1.9) add their domain-specific fields. The wrapper enforces
    the base fields below regardless of which subclass is registered.

    `cohort` is the list of contact_ids the agent may operate on. The
    wrapper filters opted-out contacts out of this list BEFORE the LLM
    sees it. The agent's tools.py is responsible for treating `cohort`
    as the only legal recipient set.

    `business_entity_id` is the natural identity of the work — what the
    mutex is scoped to. Use contact_id for outreach agents, account_id
    for scrapers, blueprint_id for build, etc.
    """

    model_config = ConfigDict(extra="allow")

    skill: str
    correlation_id: UUID
    business_entity_id: str | None = None
    cohort: list[str] = []
    trigger: str = "manual"
    client_slug: str | None = None
    # Free-form payload kept for agents not yet migrated to a tight contract.
    payload: dict[str, Any] = {}


class AgentOutput(BaseModel):
    """Base output contract every wrapped agent produces.

    Per-agent subclasses extend with the specific fields they emit. The
    wrapper enforces post-AI invariants on the base fields:

      * `confidence`     — below skill_config.min_confidence → HITL
      * `human_facing_drafts` — each entry is run through style_guard.filter;
                                a non-empty violation list → rejection
      * `recipients`     — every recipient ID must be in the cohort that
                            was passed to the agent (no LLM-invented IDs)
      * `event_to_emit`  — optional typed event for broker handoff
    """

    model_config = ConfigDict(extra="allow")

    confidence: float = 1.0
    human_facing_drafts: list[str] = []
    recipients: list[str] = []
    event_to_emit: dict[str, Any] | None = None
    notes: str | None = None


@dataclass
class AgentContract:
    """Per-agent registration: input/output schemas + value-bound spec."""

    skill: str
    input_model: type[AgentInput] = AgentInput
    output_model: type[AgentOutput] = AgentOutput
    min_confidence: float = 0.7
    requires_human_approval_above_value_usd: float | None = None
    # Optional value-extractor: given the output, return the dollar amount
    # to bound-check. None means "no value-bound check applies."
    value_extractor: Callable[[AgentOutput], float | None] | None = None
    # Per-skill mutex TTL. Default 30 min matches execute_run's wall-clock.
    mutex_ttl_seconds: int = 1800


_CONTRACTS: dict[str, AgentContract] = {}


def register_contract(contract: AgentContract) -> None:
    """Register a per-agent contract. Called from each agent's __init__.py
    after Wave 1.9 migration. Until registered, the default permissive
    AgentInput/AgentOutput contract is used."""
    _CONTRACTS[contract.skill] = contract
    log.info("agent_contract_registered", skill=contract.skill)


def get_contract(skill: str) -> AgentContract:
    """Return the registered contract, or a permissive default."""
    return _CONTRACTS.get(skill, AgentContract(skill=skill))


# ─────────────────────────── exceptions ───────────────────────────


class WrapperError(Exception):
    """Base class so callers can catch any wrapper failure with one except."""


class DuplicateInvocation(WrapperError):
    """Idempotency key already processed within the dedupe window."""


class MutexHeld(WrapperError):
    """Another worker is processing the same (skill, entity) right now."""


class CostBudgetExceeded(WrapperError):
    """Daily cost cap for (skill, client) would be exceeded."""


class StyleViolation(WrapperError):
    """One or more human-facing drafts violated style_guard."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__(f"style_guard violations: {violations[:5]}")


class RecipientNotInCohort(WrapperError):
    """LLM produced a recipient ID that wasn't in the filtered cohort.

    This is the single biggest "never AI" guard: deterministic code refuses
    to deliver to an identity the cohort filter didn't approve.
    """


class ValueBoundExceeded(WrapperError):
    """An AI-derived dollar value exceeded MAX_OPPORTUNITY_VALUE_USD
    without HITL approval. Wave 2.1."""


# ─────────────────────────── pre-AI stages ────────────────────────


def _check_idempotency(skill: str, payload: AgentInput) -> str:
    """Generate the run-level idempotency key and check it hasn't been
    used in the dedupe window.

    Returns the key (callers store it on the agent_runs row).
    Raises DuplicateInvocation if a prior run with the same key still
    exists in a non-terminal state, or completed within the window.
    """
    key = generate_run_key(
        skill=skill,
        business_entity_id=payload.business_entity_id,
        trigger=payload.trigger,
        window="minute",
    )
    try:
        resp = (
            supabase.table("agent_runs")
            .select("run_id,status")
            .eq("idempotency_key", key)
            .limit(1)
            .execute()
        )
        if resp.data:
            existing = resp.data[0]
            log.info(
                "wrapper_idempotency_dedup",
                skill=skill,
                entity=payload.business_entity_id,
                existing_run_id=existing["run_id"],
                existing_status=existing["status"],
            )
            raise DuplicateInvocation(
                f"run already exists with key {key[:12]}… ({existing['status']})"
            )
    except DuplicateInvocation:
        raise
    except Exception as exc:  # noqa: BLE001 — Supabase outage is non-fatal here
        # Fail-open: we'd rather let a possible duplicate through than refuse
        # to dispatch any work if the DB is having a bad minute. The UNIQUE
        # constraint on agent_runs.idempotency_key (migration 0040) will
        # still block the insert if the duplicate was real.
        log.warning("wrapper_idempotency_check_failed_open", skill=skill, error=str(exc))
    return key


def _filter_cohort(cohort: list[str]) -> tuple[list[str], list[str]]:
    """Drop opted-out contacts from the cohort. Returns (kept, dropped).

    This is the single place opt-out enforcement happens in the new
    architecture. The agent never sees opted-out contacts, so it cannot
    accidentally craft a message for them.
    """
    kept: list[str] = []
    dropped: list[str] = []
    for contact_id in cohort:
        if is_opted_out(contact_id):
            dropped.append(contact_id)
        else:
            kept.append(contact_id)
    if dropped:
        log.info(
            "wrapper_cohort_optout_filtered",
            kept=len(kept),
            dropped=len(dropped),
            sample_dropped=dropped[:3],
        )
    return kept, dropped


def _check_cost_budget(skill: str, client_slug: str | None) -> None:
    """Pre-flight check: are we under the per-skill, per-day budget?

    Looks at `cost_usd` rolled up in agent_runs over the last 24h. If
    the running total + this skill's estimated cost would exceed the
    daily cap, raise CostBudgetExceeded. The estimated cost defaults to
    `settings.per_run_cost_cap_usd` because we don't know the exact cost
    until the run completes — this is a guardrail, not an exact meter.

    Fail-open on DB error so transient outages don't refuse all dispatch.
    """
    daily_cap = float(getattr(settings, "per_skill_daily_cost_cap_usd", 0.0) or 0.0)
    if daily_cap <= 0:
        return  # feature disabled until Wave 3 ships per-skill caps
    try:
        from datetime import datetime, timedelta, timezone

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        q = (
            supabase.table("agent_runs")
            .select("cost_usd")
            .eq("agent_name", skill)
            .gte("created_at", cutoff)
        )
        if client_slug:
            q = q.eq("client_slug", client_slug)
        rows = q.execute().data or []
        spent = sum(float(r.get("cost_usd") or 0.0) for r in rows)
        if spent >= daily_cap:
            log.warning("wrapper_budget_exceeded", skill=skill, spent=spent, cap=daily_cap)
            raise CostBudgetExceeded(
                f"{skill} 24h spend ${spent:.2f} >= daily cap ${daily_cap:.2f}"
            )
    except CostBudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("wrapper_budget_check_failed_open", skill=skill, error=str(exc))


# ─────────────────────────── post-AI stages ───────────────────────


def _validate_output(contract: AgentContract, raw: Any) -> AgentOutput:
    """Parse the agent's final state into the registered AgentOutput model.

    `raw` is typically the LangGraph final state dict. Per-agent contracts
    override `output_model` with a tighter Pydantic class; the default is
    the permissive base.
    """
    if isinstance(raw, contract.output_model):
        return raw
    if raw is None:
        return contract.output_model()  # type: ignore[call-arg]
    # LangGraph state is dict-like; Pydantic.model_validate accepts dicts.
    return contract.output_model.model_validate(raw)  # type: ignore[no-any-return]


def _check_style(output: AgentOutput) -> None:
    """Hard gate on style_guard violations for any human-facing draft.

    Wave 1.6 made `style_guard.filter()` callable. We import lazily so a
    missing/broken style_guard does not block the wrapper from loading;
    a degraded import means no filter is applied (logged as a warning).
    """
    if not output.human_facing_drafts:
        return
    try:
        from omerion_core.outreach.style_guard import filter as style_filter
    except Exception as exc:  # noqa: BLE001
        log.warning("wrapper_style_guard_unavailable", error=str(exc))
        return

    all_violations: list[str] = []
    for draft in output.human_facing_drafts:
        try:
            ok, violations = style_filter(draft)
        except Exception as exc:  # noqa: BLE001 — never crash on the filter
            log.warning("wrapper_style_filter_error", error=str(exc))
            continue
        if not ok:
            all_violations.extend(violations)
    if all_violations:
        raise StyleViolation(all_violations)


def _verify_recipients(output: AgentOutput, cohort: list[str]) -> None:
    """Refuse delivery to identities the cohort filter didn't approve.

    This is the deterministic guarantee that an LLM cannot construct a
    contact_id out of thin air and have it survive to outbound. Every
    recipient ID the agent emits MUST be present in `cohort` (which has
    already been opt-out-filtered).
    """
    if not output.recipients:
        return
    cohort_set = set(cohort)
    invalid = [r for r in output.recipients if r not in cohort_set]
    if invalid:
        log.error("wrapper_recipient_not_in_cohort", invalid=invalid[:5], cohort_size=len(cohort))
        raise RecipientNotInCohort(
            f"output recipients not in filtered cohort: {invalid[:5]}"
        )


def _check_value_bounds(contract: AgentContract, output: AgentOutput) -> None:
    """Wave 2.1 hook: any AI-derived dollar value over the cap requires HITL.

    The contract's `value_extractor` returns the dollar value (or None if
    no value-bound check applies). If the value exceeds the cap, we raise
    ValueBoundExceeded — the caller's exception handler should re-route
    the run to HITL. Wave 2.1 wires `offer_matching` first.
    """
    if contract.value_extractor is None or contract.requires_human_approval_above_value_usd is None:
        return
    val = contract.value_extractor(output)
    if val is None:
        return
    if val > contract.requires_human_approval_above_value_usd:
        log.warning(
            "wrapper_value_bound_exceeded",
            skill=contract.skill,
            value_usd=val,
            cap=contract.requires_human_approval_above_value_usd,
        )
        raise ValueBoundExceeded(
            f"{contract.skill}: value ${val:.2f} > cap "
            f"${contract.requires_human_approval_above_value_usd:.2f}"
        )


# ─────────────────────────── public API ───────────────────────────


@dataclass
class WrapperResult:
    """What the wrapper returns to its caller."""

    run_id: str
    skill: str
    status: str  # "completed" | "failed" | "hitl_waiting" | "skipped"
    output: AgentOutput | None = None
    error: str | None = None
    idempotency_key: str = ""
    cohort_kept: list[str] = field(default_factory=list)
    cohort_dropped: list[str] = field(default_factory=list)


def run(
    skill: str,
    input_payload: AgentInput | dict[str, Any],
    *,
    source_channel: str = "wrapper",
    discord_channel_id: str | None = None,
    discord_thread_id: str | None = None,
) -> WrapperResult:
    """Single entry point for every agent invocation.

    Args:
      skill: agent registry name (kebab-case), e.g. 'linkedin-outreach'.
      input_payload: an AgentInput (or subclass) instance, or a dict that
                     the registered input_model will validate.
      source_channel: 'discord' | 'scheduler' | 'api' | 'event' | 'wrapper'.

    Returns:
      WrapperResult with run_id, final status, parsed output, and the
      kept/dropped cohort breakdown for audit.

    Raises only on programmer errors (e.g. missing skill, invalid input
    type). All operational failures — duplicate invocation, mutex held,
    cost cap, style violation, etc. — are caught and returned as
    `status="skipped"` or `status="failed"` with the error message.
    """
    contract = get_contract(skill)

    # ── Stage 1: input ──
    if isinstance(input_payload, dict):
        payload_dict = {"skill": skill, **input_payload}
        input_obj = contract.input_model.model_validate(payload_dict)
    elif isinstance(input_payload, AgentInput):
        input_obj = input_payload
    else:
        raise TypeError(f"input_payload must be AgentInput or dict, got {type(input_payload)}")

    # ── Stage 2: pre-AI ──
    try:
        idempotency_key = _check_idempotency(skill, input_obj)
    except DuplicateInvocation as exc:
        return WrapperResult(
            run_id="",
            skill=skill,
            status="skipped",
            error=str(exc),
            idempotency_key="",
        )

    try:
        _check_cost_budget(skill, input_obj.client_slug)
    except CostBudgetExceeded as exc:
        return WrapperResult(
            run_id="",
            skill=skill,
            status="skipped",
            error=str(exc),
            idempotency_key=idempotency_key,
        )

    kept, dropped = _filter_cohort(input_obj.cohort)
    input_obj.cohort = kept  # mutate so the agent sees the filtered cohort

    # Mutex scope is (skill, entity). If no entity supplied, scope to skill
    # alone — useful for cron-scheduled R&D sweeps where any concurrent run
    # would duplicate work.
    lock_name = f"agent.{skill}.{input_obj.business_entity_id or 'global'}"

    with mutex(lock_name, ttl_seconds=contract.mutex_ttl_seconds, holder_id=default_holder_id()) as acquired:
        if not acquired:
            log.info("wrapper_mutex_skip", skill=skill, lock=lock_name)
            return WrapperResult(
                run_id="",
                skill=skill,
                status="skipped",
                error=f"mutex held: {lock_name}",
                idempotency_key=idempotency_key,
                cohort_kept=kept,
                cohort_dropped=dropped,
            )

        # Create the run row with idempotency_key stamped on it.
        run_id = str(uuid4())
        run_lifecycle.create_run(
            run_id=run_id,
            agent_name=skill,
            source_channel=source_channel,
            inputs={**input_obj.model_dump(mode="json"), "session_id": run_id, "run_id": run_id},
            triggered_by=input_obj.trigger,
            discord_channel_id=discord_channel_id,
            discord_thread_id=discord_thread_id,
            correlation_id=input_obj.correlation_id,
        )
        # Stamp the key. Done via UPDATE rather than create_run kwarg so the
        # lifecycle module signature stays stable until Wave 1.8 refactor.
        try:
            supabase.table("agent_runs").update(
                {"idempotency_key": idempotency_key}
            ).eq("run_id", run_id).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("wrapper_stamp_idempotency_failed", run_id=run_id, error=str(exc))

        # ── Stage 3: AI (delegate to existing executor) ──
        execution_outcome = execute_run(run_id)

        status = execution_outcome.get("status", "failed")

        # ── Stage 4: post-AI ──
        if status == "completed":
            try:
                raw_result = execution_outcome.get("result") or execution_outcome
                output = _validate_output(contract, raw_result)
                _check_style(output)
                _verify_recipients(output, kept)
                _check_value_bounds(contract, output)
            except StyleViolation as exc:
                log.warning(
                    "wrapper_post_validation_style", run_id=run_id, violations=exc.violations[:5]
                )
                run_lifecycle.fail_run(run_id, error=f"style_violation: {exc}")
                return WrapperResult(
                    run_id=run_id,
                    skill=skill,
                    status="failed",
                    error=str(exc),
                    idempotency_key=idempotency_key,
                    cohort_kept=kept,
                    cohort_dropped=dropped,
                )
            except RecipientNotInCohort as exc:
                log.error("wrapper_post_validation_recipient", run_id=run_id, error=str(exc))
                run_lifecycle.fail_run(run_id, error=f"recipient_violation: {exc}")
                return WrapperResult(
                    run_id=run_id,
                    skill=skill,
                    status="failed",
                    error=str(exc),
                    idempotency_key=idempotency_key,
                    cohort_kept=kept,
                    cohort_dropped=dropped,
                )
            except ValueBoundExceeded as exc:
                # Route to HITL rather than fail — a human can approve over-cap.
                log.warning("wrapper_value_bound_to_hitl", run_id=run_id, error=str(exc))
                run_lifecycle.transition(
                    run_id, "hitl_waiting", extra={"error": f"value_bound: {exc}"}
                )
                return WrapperResult(
                    run_id=run_id,
                    skill=skill,
                    status="hitl_waiting",
                    error=str(exc),
                    idempotency_key=idempotency_key,
                    cohort_kept=kept,
                    cohort_dropped=dropped,
                )
            except Exception as exc:  # noqa: BLE001 — Pydantic ValidationError etc.
                log.error("wrapper_post_validation_failed", run_id=run_id, error=str(exc))
                run_lifecycle.fail_run(run_id, error=f"output_validation: {exc}")
                return WrapperResult(
                    run_id=run_id,
                    skill=skill,
                    status="failed",
                    error=str(exc),
                    idempotency_key=idempotency_key,
                    cohort_kept=kept,
                    cohort_dropped=dropped,
                )

            # Confidence threshold gate.
            if output.confidence < contract.min_confidence:
                log.info(
                    "wrapper_low_confidence_to_hitl",
                    run_id=run_id,
                    confidence=output.confidence,
                    threshold=contract.min_confidence,
                )
                run_lifecycle.transition(run_id, "hitl_waiting")
                return WrapperResult(
                    run_id=run_id,
                    skill=skill,
                    status="hitl_waiting",
                    output=output,
                    idempotency_key=idempotency_key,
                    cohort_kept=kept,
                    cohort_dropped=dropped,
                )

            # ── Stage 5: emit ──
            if output.event_to_emit:
                _emit_handoff(skill, output.event_to_emit, input_obj.correlation_id)

            return WrapperResult(
                run_id=run_id,
                skill=skill,
                status="completed",
                output=output,
                idempotency_key=idempotency_key,
                cohort_kept=kept,
                cohort_dropped=dropped,
            )

        # Pass through non-completed statuses (hitl_waiting, failed) with
        # whatever error the executor logged.
        return WrapperResult(
            run_id=run_id,
            skill=skill,
            status=status,
            error=execution_outcome.get("error"),
            idempotency_key=idempotency_key,
            cohort_kept=kept,
            cohort_dropped=dropped,
        )


def _emit_handoff(source_skill: str, event_payload: dict[str, Any], correlation_id: UUID) -> None:
    """Wave 2 will replace this with broker.emit_typed(schema, payload). For
    now we route through bus.emit_event so the existing 64 call sites and
    the broker subscriptions keep working unchanged."""
    try:
        from omerion_core.events.bus import EventType, emit_event

        event_type_raw = event_payload.get("event_type")
        if not event_type_raw:
            log.warning("wrapper_emit_missing_event_type", skill=source_skill)
            return
        try:
            event_type = EventType(event_type_raw)
        except ValueError:
            event_type = event_type_raw  # bus.emit_event accepts str
        emit_event(
            event_type,
            source_agent=source_skill,
            payload=event_payload,
            correlation_id=correlation_id,
        )
    except Exception as exc:  # noqa: BLE001 — emit failure must not crash the wrapper
        log.error("wrapper_emit_failed", skill=source_skill, error=str(exc))


__all__ = [
    "AgentInput",
    "AgentOutput",
    "AgentContract",
    "WrapperResult",
    "WrapperError",
    "DuplicateInvocation",
    "MutexHeld",
    "CostBudgetExceeded",
    "StyleViolation",
    "RecipientNotInCohort",
    "ValueBoundExceeded",
    "register_contract",
    "get_contract",
    "run",
]
