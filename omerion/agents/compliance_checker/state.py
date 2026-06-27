"""State for COMPLIANCE_CHECKER."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class ComplianceViolation(BaseModel):
    rule_id: str
    severity: Literal["critical", "warning", "info"]
    target_agent: str | None = None
    description: str


class ComplianceCheckerState(AgentRunState):
    agent_name: str = "compliance_checker"

    # ─── Scan config ─────────────────────────────────────────────
    scan_window_hours: int = 24
    weekly_report_day: int = 0  # Monday

    # ─── Scan targets ────────────────────────────────────────────
    agent_names: list[str] = Field(default_factory=list)

    # ─── Deterministic check results ─────────────────────────────
    violations: list[ComplianceViolation] = Field(default_factory=list)
    critical_count: int = 0
    warning_count: int = 0

    # ─── LLM trend report (Mondays only) ─────────────────────────
    trend_report_md: str = ""

    # ─── Output ──────────────────────────────────────────────────
    verdict: Literal["clean", "violations_found"] | None = None
