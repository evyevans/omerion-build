"""State for AUDITOR — Constitutional Guardian (RSI Agent #5).

AuditRecord captures a single row from the audit_log table —
one discrete action taken by HEALER, TRAINER, or any other
self-modifying agent.

ConstitutionalVerdict represents the AUDITOR's judgment on a
group of AuditRecords — whether they are COMPLIANT, VIOLATING
(must be reverted immediately), or SUSPICIOUS (needs HITL review).

AuditorState is the full LangGraph run state that flows across
all five nodes: scan → verify → revert → report → emit.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


# ─── Domain types ──────────────────────────────────────────────────────────


Severity = Literal["compliant", "suspicious", "critical_violation"]
TriggerMode = Literal["nightly_cron", "healing_applied", "prompt_update_proposed"]

# Constitutional rule IDs — each maps to a discrete guardrail checked in verify_node.
RuleID = Literal[
    "COST_CAP_INCREASE",       # Any change raising a cost cap > 10%
    "UNAUTHORIZED_API",        # API call to a host not on the approved whitelist
    "HITL_BYPASS",             # Self-improvement change applied without HITL approval in founder_review_queue
    "CORE_LOGIC_MUTATION",     # Modification to .py files in omerion_core/ or agents/ graph/state/tools
    "SECRET_EXPOSURE",         # Audit record contains a raw API key, token, or PII
    "SCHEMA_DRIFT",            # Supabase schema change (ADD/DROP COLUMN/TABLE) without migration trail
    "SELF_REVERT_LOOP",        # HEALER attempted to revert AUDITOR itself
]


class AuditRecord(BaseModel):
    """A single row from the audit_log table."""

    audit_id: UUID
    source_agent: str                    # e.g. "healer", "trainer"
    action_type: str                     # e.g. "config_patch", "prompt_update", "agent_revert"
    target_resource: str                 # e.g. "config/agents.yaml", "agents/crm_nurture/prompts.py"
    diff_summary: str                    # Human-readable diff or change description (≤ 2 000 chars)
    raw_payload: dict[str, Any] = Field(default_factory=dict)   # Full JSON payload as stored
    created_at: str                      # ISO-8601 UTC
    hitl_review_id: UUID | None = None   # Populated only if agent claimed HITL approval
    reverted: bool = False               # True if already reverted by a prior AUDITOR run
    requires_git_revert: bool = False    # True if the change touched a versioned file


class ConstitutionalVerdict(BaseModel):
    """AUDITOR's verdict on a single AuditRecord."""

    audit_id: UUID
    severity: Severity
    rules_violated: list[RuleID] = Field(default_factory=list)
    revert_executed: bool = False
    revert_error: str | None = None
    verdict_reasoning: str = ""          # One-paragraph plain-English explanation (AUDITOR writes this)


class WeeklyComplianceSummary(BaseModel):
    """Structured weekly report delivered to the founder via Discord + Supabase."""

    report_date: date
    window_days: int
    total_records_scanned: int
    compliant_count: int
    suspicious_count: int
    critical_violation_count: int
    reverted_count: int
    top_offending_agents: list[str] = Field(default_factory=list)
    narrative_md: str = ""               # Claude-authored Markdown summary (≤ 600 words)


class AuditorState(AgentRunState):
    """Full run state for the AUDITOR LangGraph graph."""

    agent_name: str = "auditor"

    # ─── Trigger context ─────────────────────────────────────────────────────
    trigger_mode: TriggerMode = "nightly_cron"
    triggering_event_id: str | None = None  # Set when trigger_mode != nightly_cron

    # ─── Scan window config ──────────────────────────────────────────────────
    scan_window_hours: int = 24             # For nightly_cron; overridden to 0 for event triggers
    weekly_report_day: int = 0             # 0 = Monday (ISO weekday). Weekly report only on this day.

    # ─── Scan results ────────────────────────────────────────────────────────
    audit_records: list[AuditRecord] = Field(default_factory=list)
    records_scanned: int = 0

    # ─── Verdicts ────────────────────────────────────────────────────────────
    verdicts: list[ConstitutionalVerdict] = Field(default_factory=list)
    critical_violations: list[ConstitutionalVerdict] = Field(default_factory=list)
    suspicious_flags: list[ConstitutionalVerdict] = Field(default_factory=list)

    # ─── Revert tracking ─────────────────────────────────────────────────────
    reverts_attempted: int = 0
    reverts_succeeded: int = 0
    reverts_failed: int = 0

    # ─── Weekly report ───────────────────────────────────────────────────────
    weekly_report: WeeklyComplianceSummary | None = None
    weekly_report_id: UUID | None = None

    # ─── RAG context ─────────────────────────────────────────────────────────
    # Past violation snippets from infra_violations namespace — advisory only.
    # Injected into VERIFY_USER prompt; cannot override deterministic verdicts.
    violation_context: list[str] = Field(default_factory=list)

    # ─── HITL escalation ─────────────────────────────────────────────────────
    # Populated when suspicious (not critical) verdicts require founder review.
    suspicious_review_ids: list[UUID] = Field(default_factory=list)
