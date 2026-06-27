"""State for DEPLOYER — Infrastructure Provisioner (Agentic Factory Agent #18).

Pipeline: backup_database → run_database_migrations → provision_cloud_run
          → update_dns → run_smoke_tests → (rollback?) → emit

Guardrails enforced at the graph level:
  1. backup_ref must be set before any migration runs.
  2. provision_cloud_run never runs if migration_ok is False.
  3. smoke_ok is set to False on any response that is not HTTP 200
     within 60 seconds, immediately triggering the rollback branch.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from omerion_core.state.base import AgentRunState


class DeployerState(AgentRunState):
    agent_name: str = "deployer"

    # ─── Input (from deployment.live event) ─────────────────────
    deployment_id: UUID
    client_id: UUID
    blueprint_id: UUID | None = None

    # ─── Pipeline outputs (populated as each node succeeds) ──────
    backup_ref: str | None = None       # Supabase PITR backup ID
    migrations_approved: bool = False    # founder G3 approval of pending migration SQL
    migration_ok: bool = False
    migration_error: str | None = None
    provision_ok: bool = False
    live_url: str | None = None         # Cloud Run / Railway URL
    health_url: str | None = None       # live_url + health_path
    smoke_ok: bool = False
    smoke_status_code: int | None = None
    smoke_attempts: int = 0            # incremented on each smoke_attempt_node execution
    smoke_done: bool = False           # True when result is final (success or terminal failure)
    rollback_attempted: bool = False
    rollback_ok: bool | None = None

    # ─── Terminal outcome ────────────────────────────────────────
    outcome: Literal[
        "confirmed", "health_failed", "rollback_ok", "rollback_failed"
    ] | None = None
    failure_reason: str | None = None   # migration_error | provision_error | smoke_timeout | rollback_failed
