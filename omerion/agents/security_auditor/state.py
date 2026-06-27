"""State for SECURITY_AUDITOR."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class SecurityFinding(BaseModel):
    finding_type: Literal["secret", "dependency_cve", "exposed_endpoint", "config_drift"]
    severity: Literal["critical", "high", "medium", "low"]
    resource: str
    description: str
    cve_id: str | None = None
    remediation: str | None = None


class SecurityAuditorState(AgentRunState):
    agent_name: str = "security_auditor"

    # ─── Scan results ─────────────────────────────────────────────
    findings: list[SecurityFinding] = Field(default_factory=list)
    critical_count: int = 0
    high_count: int = 0

    # ─── LLM brief (weekly only) ──────────────────────────────────
    security_brief_md: str = ""
    weekly_report_day: int = 0  # Monday

    # ─── Verdict ─────────────────────────────────────────────────
    verdict: Literal["passed", "critical_found"] | None = None
