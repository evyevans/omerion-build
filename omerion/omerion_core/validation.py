"""
OMERION Backbone — Enum Validation Layer (A1)
=============================================
Single source of truth for all constrained field values.
Every agent and tool MUST import from here before writing to Supabase.
Never hardcode enum strings outside this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── Canonical enums ─────────────────────────────────────────────────────────

PERSONAS: frozenset[str] = frozenset({
    "Operations Leader",
    "Revenue Leader",
    "SME Founder",
    "Agency Owner",
    "E-commerce Operator",
    "Professional Services Owner",
    "SaaS Founder",
    "HR / Talent Leader",
    "Finance Operations Leader",
    "NEEDS_REVIEW",
})

COMPANY_TYPES: frozenset[str] = frozenset({
    "Independent SMB (10-50 employees)",
    "Growth-Stage Startup (Series A/B)",
    "Digital Marketing Agency",
    "Consulting Firm",
    "E-commerce Brand (DTC)",
    "SaaS Company",
    "Law Firm",
    "Accounting / Finance Firm",
    "Staffing / HR Firm",
    "Manufacturing Company",
    "Healthcare Practice",
    "Professional Services Firm",
    "Media / Content Company",
    "Nonprofit Organization",
    "NEEDS_REVIEW",
})

CONTACT_STAGES: frozenset[str] = frozenset({
    "new",
    "enriched",
    "outreach_sent",
    "replied",
    "meeting_booked",
    "proposal_sent",
    "closed_won",
    "closed_lost",
    "do_not_contact",
    "needs_review",
})

DEAL_STAGES: frozenset[str] = frozenset({
    "Discovery",
    "Proposal",
    "Negotiation",
    "Closed Won",
    "Closed Lost",
})

CHANNELS: frozenset[str] = frozenset({
    "email",
    "linkedin",
    "linkedin_dm",
    "sms",
})

SENTIMENT_VALUES: frozenset[str] = frozenset({
    "POSITIVE",
    "WARM",
    "NEUTRAL",
    "NEGATIVE",
    "REFERRAL",
})

REVIEW_STATUSES: frozenset[str] = frozenset({
    "pending",
    "approved",
    "rejected",
    "sent",
    "send_failed",
})


# ── Validation result + strict-mode exception (Wave 1.7) ─────────────────────


class ValidationFailed(Exception):
    """Raised by validators in `strict=True` mode (Wave 1.7).

    The wrapper catches this in Stage 4 (post-AI validation) and routes
    the run to HITL with the failing field, value, and valid_values
    attached so the founder can see exactly what the LLM produced.

    Soft validation (the historical behaviour) still returns a
    `ValidationResult` with a NEEDS_REVIEW fallback for callers that
    aren't ready to migrate.
    """

    def __init__(self, field: str, value: Any, valid_values: set[str] | None = None):
        self.field = field
        self.value = value
        self.valid_values = set(valid_values) if valid_values else set()
        super().__init__(
            f"validation failed on field='{field}': value={value!r} "
            f"not in {sorted(self.valid_values) if self.valid_values else '<set>'}"
        )


@dataclass
class ValidationResult:
    valid: bool
    value: str
    error: str | None = None

    def raise_if_invalid(self) -> None:
        if not self.valid:
            raise ValueError(self.error)


def _validate_against(
    *,
    field: str,
    value: Any,
    valid_set: frozenset[str],
    fallback: str,
    strict: bool,
    normalize: str = "strip",  # 'strip' | 'lower' | 'upper'
) -> ValidationResult:
    """Internal helper — single code path for all enum validators.

    On invalid:
      * strict=True  → raise ValidationFailed
      * strict=False → return ValidationResult(valid=False, value=fallback)
    """
    if not value:
        if strict:
            raise ValidationFailed(field, value, set(valid_set))
        return ValidationResult(
            valid=False, value=fallback,
            error=f"{field} is null/empty — defaulting to {fallback}",
        )
    v = str(value).strip()
    if normalize == "lower":
        v = v.lower()
    elif normalize == "upper":
        v = v.upper()
    if v in valid_set:
        return ValidationResult(valid=True, value=v)
    if strict:
        raise ValidationFailed(field, value, set(valid_set))
    return ValidationResult(
        valid=False, value=fallback,
        error=f"'{v}' is not a valid {field}.",
    )


# ── Validators ───────────────────────────────────────────────────────────────
#
# All validators accept `strict=False` for backwards-compat. Pass `strict=True`
# from the wrapper / post-AI validation so a bad LLM output raises rather than
# silently coerces to NEEDS_REVIEW.


def validate_persona(value: Any, *, strict: bool = False) -> ValidationResult:
    """Validate a persona value against the canonical 9+1 enum."""
    return _validate_against(
        field="persona", value=value, valid_set=PERSONAS,
        fallback="NEEDS_REVIEW", strict=strict,
    )


def validate_company_type(value: Any, *, strict: bool = False) -> ValidationResult:
    """Validate a company_type value against the canonical 14+1 enum."""
    return _validate_against(
        field="company_type", value=value, valid_set=COMPANY_TYPES,
        fallback="NEEDS_REVIEW", strict=strict,
    )


def validate_sentiment(value: Any, *, strict: bool = False) -> ValidationResult:
    """Validate sentiment from Analyst output. Must be one of 5 values."""
    return _validate_against(
        field="sentiment", value=value, valid_set=SENTIMENT_VALUES,
        fallback="NEUTRAL", strict=strict, normalize="upper",
    )


def validate_channel(value: Any, *, strict: bool = False) -> ValidationResult:
    """Validate a channel value."""
    return _validate_against(
        field="channel", value=value, valid_set=CHANNELS,
        fallback="", strict=strict, normalize="lower",
    )


def validate_stage(value: Any, *, strict: bool = False) -> ValidationResult:
    """Validate a contact stage value."""
    return _validate_against(
        field="stage", value=value, valid_set=CONTACT_STAGES,
        fallback="needs_review", strict=strict, normalize="lower",
    )


def validate_deal_stage(value: Any, *, strict: bool = False) -> ValidationResult:
    """Validate a deal stage. Case-preserving (Discovery vs discovery)."""
    return _validate_against(
        field="deal_stage", value=value, valid_set=DEAL_STAGES,
        fallback="Discovery", strict=strict,
    )


def validate_review_status(value: Any, *, strict: bool = False) -> ValidationResult:
    """Validate a HITL review status."""
    return _validate_against(
        field="review_status", value=value, valid_set=REVIEW_STATUSES,
        fallback="pending", strict=strict, normalize="lower",
    )
