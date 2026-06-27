"""Record real business outcomes — the things Boris and Elon both said matter.

Agents that produce a fact tied to money or pipeline progress (booked demo,
qualified lead, signed contract, reply received) call `record_outcome` once
the side-effectful action lands. Mission Control reads from this table to
compute "outcomes today" — not "runs today," because runs without outcomes
mean the system burned tokens without producing value.

**Wave 2.3 — source-of-truth gate (the most important write-safety change):**

Every revenue-carrying outcome row MUST declare its `source`. The only
legal sources are:
  * `stripe`              — set by the Stripe webhook handler
  * `crm_manual`          — set by a human via the dashboard / API
  * `deterministic_compute` — set by a deterministic post-processor that
                              joins Stripe + agent_runs (e.g. attribution
                              reports computed from real invoice data)

An agent's LLM CANNOT produce a `signed_contract` or `closed_won` outcome
because no agent has access to a legal source value. The function raises
`UnauthorizedOutcomeSource` if any call attempts to bypass the gate — this
exception is intentionally NOT caught inside this module so the caller is
forced to deal with it (loud failure beats silent revenue corruption).

The gate is paired with two DB constraints (migration 0040):
  * UNIQUE(run_id, outcome_type) — same run cannot record the same outcome
    twice (e.g. retried agent inflating revenue)
  * UNIQUE(idempotency_key) — same logical outcome cannot be duplicated
    across runs (e.g. Stripe webhook fired twice)
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.util.idempotency import generate_key

log = get_logger("omerion.business_outcomes")


VALID_OUTCOME_TYPES = {
    "qualified_lead",
    "booked_demo",
    "proposal_sent",
    "signed_contract",
    "closed_won",
    "closed_lost",
    "reply_received",
    "meeting_completed",
}

# Wave 2.3: the *only* legal sources. Anything else (e.g. an agent passing
# `source="agent_inference"`) raises UnauthorizedOutcomeSource immediately.
LegalSource = Literal["stripe", "crm_manual", "deterministic_compute"]
_LEGAL_SOURCES: frozenset[str] = frozenset({"stripe", "crm_manual", "deterministic_compute"})


# Outcome types that materially affect revenue reporting. These are the ones
# we are most aggressive about — they require both a `source` declaration AND
# a `value_usd` figure for non-zero impact tracking.
_REVENUE_BEARING_TYPES: frozenset[str] = frozenset({
    "signed_contract", "closed_won", "closed_lost", "proposal_sent",
})


class UnauthorizedOutcomeSource(ValueError):
    """Raised when an outcome write attempts to use an illegal source.

    Wave 2.3 contract: only `stripe`, `crm_manual`, and
    `deterministic_compute` may produce revenue-bearing outcomes. Any other
    source string — including a sentinel `agent_inference` an agent might
    pass — is rejected at the function boundary. The caller is responsible
    for proving their source is legal; the function does not "best-effort"
    its way around the missing declaration.
    """


def record_outcome(
    *,
    outcome_type: str,
    source: LegalSource,
    run_id: UUID | str | None = None,
    correlation_id: UUID | str | None = None,
    contact_id: UUID | str | None = None,
    account_id: UUID | str | None = None,
    opportunity_id: UUID | str | None = None,
    value_usd: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Insert a single outcome row, gated by the source-of-truth contract.

    Args:
      outcome_type: one of `VALID_OUTCOME_TYPES`.
      source: required. One of {'stripe', 'crm_manual', 'deterministic_compute'}.
              Agents that try to pass 'agent_inference' or any other string
              get `UnauthorizedOutcomeSource` raised — no silent coercion.
      run_id / correlation_id / contact_id / etc.: provenance.
      value_usd: required for revenue-bearing outcomes when `source != 'crm_manual'`.
                 (A human can record a $0 outcome manually; an agent or Stripe
                 cannot record a `signed_contract` without a number.)
      metadata: extra fields. Stored as JSONB.

    Returns the outcome_id on success or None on a write failure (the
    write itself is best-effort to avoid breaking the agent that produced
    the underlying business action). The *contract validation* is loud:
    UnauthorizedOutcomeSource propagates so the caller has to fix the
    bug, not silently lose telemetry.
    """
    # ── contract validation (loud) ──────────────────────────────────────
    if outcome_type not in VALID_OUTCOME_TYPES:
        log.warning("business_outcome_unknown_type", outcome_type=outcome_type)
        return None

    if source not in _LEGAL_SOURCES:
        log.error(
            "business_outcome_illegal_source",
            outcome_type=outcome_type,
            source=source,
            run_id=str(run_id) if run_id else None,
        )
        raise UnauthorizedOutcomeSource(
            f"source={source!r} is not legal for business_outcomes. "
            f"Allowed: {sorted(_LEGAL_SOURCES)}. "
            f"Agent inference is never a legal source — see Wave 2.3 plan."
        )

    if outcome_type in _REVENUE_BEARING_TYPES and source != "crm_manual" and value_usd is None:
        raise UnauthorizedOutcomeSource(
            f"outcome_type={outcome_type!r} from source={source!r} requires a "
            f"non-None value_usd. Only crm_manual may omit value_usd."
        )

    # ── idempotency key ─────────────────────────────────────────────────
    # The combination that makes a logical outcome unique:
    #   (outcome_type, source, opportunity_id OR contact_id, day-bucket).
    # Same Stripe webhook fires twice → same key → DB UNIQUE rejects #2.
    # Same agent retries → same key → DB UNIQUE rejects #2.
    natural_identity = {
        "outcome_type": outcome_type,
        "source": source,
        "opportunity_id": str(opportunity_id) if opportunity_id else None,
        "contact_id": str(contact_id) if contact_id else None,
    }
    idempotency_key = generate_key(
        scope="business_outcome",
        payload=natural_identity,
        window="day",
    )

    row = {
        "outcome_type": outcome_type,
        "run_id": str(run_id) if run_id else None,
        "correlation_id": str(correlation_id) if correlation_id else None,
        "contact_id": str(contact_id) if contact_id else None,
        "account_id": str(account_id) if account_id else None,
        "opportunity_id": str(opportunity_id) if opportunity_id else None,
        "value_usd": float(value_usd) if value_usd is not None else None,
        "idempotency_key": idempotency_key,
        "metadata": {
            **(metadata or {}),
            "source": source,            # Always recorded for audit
        },
    }

    # ── best-effort write (loud on contract, quiet on transient DB) ──────
    try:
        resp = supabase.table("business_outcomes").insert(row).execute()
    except Exception as exc:  # noqa: BLE001 — best-effort
        # A failed write is logged loudly but doesn't propagate. The actual
        # business event (e.g., a sent email, a paid invoice) has already
        # happened; the outcome log is for visibility.
        log.warning(
            "business_outcome_insert_failed",
            outcome_type=outcome_type,
            source=source,
            error=str(exc),
            error_class=type(exc).__name__,
        )
        return None

    if not resp.data:
        return None

    log.info(
        "business_outcome_recorded",
        outcome_type=outcome_type,
        source=source,
        run_id=str(run_id) if run_id else None,
        value_usd=value_usd,
        idempotency_key=idempotency_key[:12] + "…",
    )
    return resp.data[0].get("outcome_id")


__all__ = [
    "VALID_OUTCOME_TYPES",
    "LegalSource",
    "UnauthorizedOutcomeSource",
    "record_outcome",
]
