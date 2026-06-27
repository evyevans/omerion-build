"""Meeting Intelligence — wrapper contract + schemas (Wave 1.9 + 2.4).

Two roles in one file:

  1. The wrapper contract (Wave 1.9) — registers MeetingIntelligenceInput
     and MeetingIntelligenceOutput with `agent_wrapper`. The confidence
     floor is 0.70 (blueprints commit to a 30/60/90 plan; sub-threshold
     drafts route to HITL).

  2. The `HitlFlags` schema (Wave 2.4) — replaces the freeform JSONB
     payload that the LLM previously dumped into `blueprints.hitl_flags`
     with a typed model. The persist node calls `validate_hitl_flags()`
     before inserting; malformed structure raises `ValidationFailed` and
     the run is routed to HITL with the bad payload attached.

A blueprint without typed hitl_flags can corrupt the founder review UI
(the dashboard expects specific keys) and the build_orchestrator
(downstream consumer that reads the flags to decide phase ordering).
"""
from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, ValidationError

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)


# ─────────────────────────── input/output ─────────────────────────


class MeetingIntelligenceInput(AgentInput):
    skill: Literal["meeting-intelligence"] = "meeting-intelligence"
    meeting_id: str = Field(min_length=1)
    source: Literal["fireflies", "manual", "zoom", "google_meet"] = "fireflies"


class MeetingIntelligenceOutput(AgentOutput):
    blueprint_id: str | None = None
    persona_detected: str | None = None
    hitl_flags_count: int = 0


# ─────────────────────────── HitlFlags schema (Wave 2.4) ───────────


# Canonical flag labels — single source of truth.
# KNOWN_FLAG_LABELS is derived from FlagLabel so they can never diverge.
# agents.yaml hitl_flag_conditions is the runtime-tunable allowlist (a
# strict subset); contracts.py is the structural enforcement layer.
# These 6 labels match both prompts.py HITL_FLAG_SYSTEM and agents.yaml exactly.
FlagLabel = Literal[
    "low_transcript_confidence",
    "ambiguous_budget",
    "unclear_timeline",
    "conflicting_stakeholder_input",
    "scope_exceeds_pricing_band",
    "persona_tier_mismatch",
]
KNOWN_FLAG_LABELS: frozenset[str] = frozenset(get_args(FlagLabel))


class HitlFlag(BaseModel):
    """One reason a blueprint needs founder review.

    `label` is one of the FlagLabel literals (Pydantic-enforced). `severity`
    drives the Discord card color (low=blue, medium=yellow, high=red).
    `evidence` is a short citation from the transcript — required so a founder
    reviewing the card has the source rather than just a label.
    """

    label: FlagLabel
    severity: Literal["low", "medium", "high"] = "medium"
    evidence: str = Field(min_length=1, max_length=500)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class HitlFlags(BaseModel):
    """The validated structure for `blueprints.hitl_flags` JSONB."""

    flags: list[HitlFlag] = Field(default_factory=list, max_length=20)
    requires_review: bool = False

    def to_jsonb(self) -> list[dict[str, Any]]:
        """Serialize to the JSONB shape the DB column expects."""
        return [f.model_dump(mode="json") for f in self.flags]


def validate_hitl_flags(payload: Any) -> HitlFlags:
    """Parse arbitrary input into a HitlFlags model.

    Accepts the legacy shape (`list[str]` — flag labels only) for
    backwards-compat: each string becomes a HitlFlag with default
    severity='medium' and evidence='(legacy: no evidence captured)'.
    The new shape is a list of HitlFlag-shaped dicts or a HitlFlags dict.

    Raises pydantic `ValidationError` on malformed input. The persist
    node catches this and routes the run to HITL with the bad payload
    attached so the founder can see what the LLM produced.
    """
    if isinstance(payload, HitlFlags):
        return payload
    if payload is None or payload == []:
        return HitlFlags(flags=[], requires_review=False)

    # Legacy list[str] shape — coerce to typed.
    # Filter labels not in KNOWN_FLAG_LABELS so stale labels from old blueprints
    # don't fail Pydantic validation (they're silently dropped, not raised).
    if isinstance(payload, list) and all(isinstance(x, str) for x in payload):
        flags = [
            HitlFlag(label=lbl, severity="medium", evidence="(legacy: no evidence captured)")
            for lbl in payload
            if lbl and lbl in KNOWN_FLAG_LABELS
        ]
        return HitlFlags(flags=flags, requires_review=bool(flags))

    # New shape: list of dicts.
    if isinstance(payload, list):
        try:
            flags = [HitlFlag.model_validate(x) for x in payload]
        except ValidationError:
            raise
        return HitlFlags(flags=flags, requires_review=bool(flags))

    # Dict shape (full HitlFlags object).
    if isinstance(payload, dict):
        return HitlFlags.model_validate(payload)

    raise ValueError(f"unsupported hitl_flags payload type: {type(payload)}")


# ─────────────────────────── contract registration ─────────────────


CONTRACT = AgentContract(
    skill="meeting-intelligence",
    input_model=MeetingIntelligenceInput,
    output_model=MeetingIntelligenceOutput,
    min_confidence=0.70,
    mutex_ttl_seconds=3600,  # meetings are larger transcripts
)


def _register() -> None:
    register_contract(CONTRACT)


_register()


__all__ = [
    "MeetingIntelligenceInput",
    "MeetingIntelligenceOutput",
    "CONTRACT",
    "FlagLabel",
    "HitlFlag",
    "HitlFlags",
    "KNOWN_FLAG_LABELS",
    "validate_hitl_flags",
]
