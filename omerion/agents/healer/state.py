"""State for HEALER — Autonomous Remediation Engine (RSI Agent #16).

Flows through 4 nodes:
    diagnose_root_cause → formulate_remediation → apply_fix → emit_healing_status

HITL gate fires between formulate_remediation and apply_fix when severity is
CRITICAL and diagnosis_confidence < 0.70 after 2 attempts.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field, computed_field

from omerion_core.state.base import AgentRunState


RemediationType = Literal["config_patch", "prompt_update", "escalated"]
Severity = Literal["low", "medium", "high", "critical"]


class HealerState(AgentRunState):
    """Full run state for HEALER."""

    agent_name: str = "healer"

    # ─── Trigger payload ─────────────────────────────────────────────────────
    failing_agent: str
    severity: Severity
    metric: str                                      # e.g. "error_rate", "latency_ms"
    metric_value: float
    alert_run_id: str | None = None

    # ─── Loop guard ──────────────────────────────────────────────────────────
    recent_fix_count: int = 0       # populated by loop_check node from healer_recent_fixes view
    rag_context_hits: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[misc]
    @property
    def loop_guard_active(self) -> bool:
        return self.recent_fix_count >= 2

    # ─── Diagnosis ───────────────────────────────────────────────────────────
    recent_telemetry: list[dict[str, Any]] = Field(default_factory=list)
    error_samples: list[dict[str, Any]]    = Field(default_factory=list)
    recent_runs: list[dict[str, Any]]      = Field(default_factory=list)
    root_cause: str | None = None
    diagnosis_attempts: int = 0
    diagnosis_confidence: float = 0.0

    # ─── Remediation plan ────────────────────────────────────────────────────
    remediation_type: RemediationType | None = None
    target_resource: str | None = None               # path relative to omerion/
    patch_description: str | None = None
    patch_yaml_key: str | None = None                # dotted key, e.g. "agents.crm_nurture.backoff_seconds"
    patch_yaml_value: Any = None                     # new value to write
    patch_skill_content: str | None = None           # full new content if prompt_update
    backup_path: str | None = None

    # ─── HITL ────────────────────────────────────────────────────────────────
    review_id: UUID | None = None
    hitl_decision: Literal["approved", "rejected"] | None = None

    # ─── Outcome ─────────────────────────────────────────────────────────────
    fix_applied: bool = False
    audit_id: UUID | None = None
    healing_notes: str = ""

    @computed_field  # type: ignore[misc]
    @property
    def requires_hitl_escalation(self) -> bool:
        return (
            self.severity == "critical"
            and self.diagnosis_confidence < 0.70
            and self.diagnosis_attempts >= 2
        )
