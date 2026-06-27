"""State for QA_TESTER agent."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState


class TestResult(BaseModel):
    name: str
    status: Literal["passed", "failed", "error", "skipped"]
    duration_ms: float = 0.0
    error_message: str | None = None


class QATesterState(AgentRunState):
    agent_name: str = "qa_tester"

    # ─── Input ───────────────────────────────────────────────────
    # Primary: direct task UUID (preferred when available)
    build_task_id: UUID | None = None
    # Fallback composite key when builder omits task_id from BUILD_TASK_COMPLETED.
    # fetch_build_context_node resolves build_task_id via deployment_id + task_slug.
    deployment_id: UUID | None = None
    task_slug: str = ""
    test_command: str = "pytest"
    coverage_threshold: float = 0.70

    # ─── Loaded context ─────────────────────────────────────────
    spec_md: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)

    # ─── Test execution results ──────────────────────────────────
    tests_total: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    coverage_pct: float = 0.0
    test_results: list[TestResult] = Field(default_factory=list)
    raw_output: str = ""

    # ─── LLM analysis (only on failure) ─────────────────────────
    failure_summary: str = ""

    # ─── Gate verdict ────────────────────────────────────────────
    verdict: Literal["passed", "failed"] | None = None
    hitl_review_id_str: str | None = None
