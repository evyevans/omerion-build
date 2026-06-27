"""State for Build Orchestrator (Agent #9).

Dual-mode:
- `internal`: improving Omerion's own OS (agents, pipelines, R&D output).
  Artifacts: GitHub PRs + deployments.
- `client`  : building a deliverable for a paying client.
  Artifacts: GitHub PRs for code + Google Docs in a per-client Drive
  folder (proposal / SOW / blueprint / weekly_update / handoff).
"""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from omerion_core.state.base import AgentRunState

TaskStatus = Literal[
    "pending",
    "issue_created",
    "branch_open",
    "pr_open",
    "ci_pass",
    "ci_fail",
    "merged",
    "deployed",
    "failed",
]

BuildMode = Literal["internal", "client"]
DocType = Literal["proposal", "sow", "blueprint", "weekly_update", "handoff"]


class TaskSpec(BaseModel):
    slug: str
    title: str
    phase: Literal["phase_1", "phase_2", "phase_3"]
    rationale: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    effort_days: float = 1.0
    depends_on: list[str] = Field(default_factory=list)
    service_package: str | None = None     # one of the 4 RE consulting packages
    files_touched_estimate: int = 1

    module: str | None = None
    task_id: UUID | None = None
    status: TaskStatus = "pending"
    issue_number: int | None = None
    branch_name: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    ci_status: str | None = None
    notes: str = ""


class ClientDeliverable(BaseModel):
    doc_type: DocType
    doc_id: str | None = None          # Google Doc id
    doc_url: str | None = None
    status: Literal["pending", "created", "failed"] = "pending"


class BuildState(AgentRunState):
    agent_name: str = "build_orchestrator"

    blueprint_id: UUID
    mode: BuildMode = "internal"
    client_id: UUID | None = None
    client_slug: str                      # "omerion-internal" for internal mode
    repo_full_name: str
    tasks: list[TaskSpec] = Field(default_factory=list)
    deployment_id: UUID | None = None
    deployment_hitl_review_id: UUID | None = None
    deployment_approved: bool = False
    deployment_status: Literal["pending", "queued", "live", "failed"] = "pending"
    rollback_url: str = ""

    # Client-mode only
    drive_folder_id: str | None = None
    deliverables: list[ClientDeliverable] = Field(default_factory=list)

    # ─── Send API fan-out ─────────────────────────────────────
    # current_task: receives one TaskSpec per Send branch
    current_task: TaskSpec | None = None

    # built_tasks: fan-in accumulator with slug-keyed merge reducer so that
    # a task appearing in multiple fan-out rounds (e.g., if state is partially
    # replayed) keeps only the latest result.
    built_tasks: Annotated[
        list[TaskSpec],
        lambda old, new: list({t.slug: t for t in [*old, *new]}.values()),
    ] = Field(default_factory=list)
