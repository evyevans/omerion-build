"""State for VALIDATOR."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class LineComment(BaseModel):
    path: str
    line: int
    body: str


class ValidatorState(AgentRunState):
    agent_name: str = "validator"

    # ─── Input ───────────────────────────────────────────────────
    pr_url: str
    pr_number: int
    repo_full: str          # "owner/repo"
    head_branch: str = ""

    # ─── Loaded from Supabase ────────────────────────────────────
    task_id: UUID | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    spec_md: str = ""

    # ─── Diff analysis ───────────────────────────────────────────
    diff_files: list[str] = Field(default_factory=list)
    diff_patch: str = ""
    diff_chunks: list[str] = Field(default_factory=list)   # per-file diff slices
    lint_errors: list[str] = Field(default_factory=list)

    # ─── LLM verdict ─────────────────────────────────────────────
    verdict: Literal["approve", "reject"] | None = None
    review_body: str = ""
    line_comments: list[LineComment] = Field(default_factory=list)

    # ─── Rejection-loop escalation (actionable: override-approve / abandon) ──
    needs_decision: bool = False              # rejection count hit the cap → ask founder
    escalation_review_id: str | None = None
    founder_overridden: bool = False          # founder forced an approve
    task_abandoned: bool = False              # founder abandoned the task
