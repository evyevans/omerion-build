"""State for BUILDER (Agent #11)."""
from __future__ import annotations

import operator
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

BuilderTaskStatus = Literal[
    "pending",
    "in_progress",
    "pr_open",
    "failed",
]


class TaskResult(BaseModel):
    """Runtime tracking record for a single task being built."""
    slug: str
    task_id: UUID
    branch_name: str
    title: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    rationale: str = ""
    spec_md: str = ""

    # execution tracking
    attempts: int = 0
    status: BuilderTaskStatus = "pending"
    last_test_output: str = ""
    commit_sha: str | None = None   # SHA of the GitHub commit batch; None until committed
    pr_number: int | None = None
    pr_url: str | None = None
    notes: str = ""


class BuilderState(AgentRunState):
    agent_name: str = "builder"

    blueprint_id: UUID
    deployment_id: UUID
    repo_full_name: str = ""  # filled from settings.github_build_repo

    tasks: list[TaskResult] = Field(default_factory=list)
    failed_slugs: list[str] = Field(default_factory=list)

    # HITL — failure escalation (NOT an approval gate; builder only opens PRs).
    builder_hitl_review_id: UUID | None = None
    retry_requested: bool = False        # founder chose "retry" at the escalation
    founder_retry_count: int = 0         # capped re-runs triggered by the founder

    # ─── Send API fan-out ─────────────────────────────────────
    # current_task_result: the task received via Send payload for this branch
    current_task_result: TaskResult | None = None

    # completed_tasks: fan-in accumulator. Custom reducer keeps latest result per
    # slug so that a founder-retry doesn't duplicate entries.
    completed_tasks: Annotated[
        list[TaskResult],
        lambda old, new: list({t.slug: t for t in [*old, *new]}.values()),
    ] = Field(default_factory=list)
