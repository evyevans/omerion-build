"""LangGraph for DEPLOYER — Infrastructure Provisioner (Agentic Factory Agent #18).

Flow (event-triggered: deployment.live):
    backup_database
      → discover_migrations       (find pending SQL; code-only deploys skip the gate)
      → [hitl_gate]               (G3 — founder approves the migration SQL before it runs)
      → apply_migrations          (HALT to emit if migration_ok is False)
      → provision_cloud_run
      → update_dns                (resolves health_url)
      → run_smoke_tests           (60 s timeout → rollback branch on failure)
      ↓ [route_after_smoke]
      → rollback                  (deterministic: restore DB then revert container)
      → emit                      (deployment.health_confirmed OR deployment.health_failed)

Design decisions:
  - All three hard guardrails are enforced at the graph level, not only in
    the prompt. Code enforcement (not just prompt engineering) means the LLM
    cannot reason its way around a safety rule.
  - route_after_smoke is a conditional edge — rollback is a branch, not a
    separate graph. This keeps the checkpointer happy: a crash mid-rollback
    replays from the last checkpoint automatically.
  - HITL gate (G3): a migration is a distinct irreversible action, so when
    pending migrations exist the founder approves the actual SQL BEFORE it runs
    (after the backup, before apply). Code-only deploys (no pending migrations)
    flow through automatically — the orchestrator already gated the merge.
  - Rollback is fully DETERMINISTIC (no LLM in the incident path): restore DB
    before reverting container, to the known-good pre-deploy state.
  - Everything else is API calls + boolean state transitions.
"""
from __future__ import annotations

import time

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.policy import Gate, ReviewItem, gate
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .state import DeployerState
from .tools import (
    backup_database,
    discover_pending_migrations,
    load_deployment,
    persist_health_log,
    provision_railway,
    restore_database_pitr,
    rollback_to_previous,
    run_migrations,
    smoke_test_once,
    update_deployment_status,
)

log = get_logger("omerion.agents.deployer")

# ─── Pending migration SQL is fetched from Supabase migrations table or disk ─
# In the current architecture the migration SQL is stored as a file on disk.
# A future iteration can fetch pending migrations from a migrations registry.
_HEALTH_PATH = "/api/v1/health"


# ─── Node 1: Backup ─────────────────────────────────────────────────────────


@traced_node("backup_database")
def backup_database_node(state: DeployerState) -> DeployerState:
    """Guardrail #1: backup must succeed before any migration runs."""
    ok, backup_ref = backup_database(state.deployment_id)
    if not ok or backup_ref is None:
        # Hard stop — do not proceed to migrations without a backup.
        raise RuntimeError(
            f"[deployer] Database backup failed for deployment {state.deployment_id}. "
            "Aborting pipeline. Check Supabase Management API credentials."
        )
    state.backup_ref = backup_ref
    update_deployment_status(state.deployment_id, "deploying")
    return state


# ─── Node 2: Migrations ─────────────────────────────────────────────────────


@traced_node("discover_migrations")
def discover_migrations_node(state: DeployerState) -> DeployerState:
    """Find pending migration SQL (runs AFTER the backup, BEFORE the G3 gate)."""
    try:
        migrations = discover_pending_migrations()
    except RuntimeError as exc:
        state.migration_ok = False
        state.migration_error = str(exc)
        state.failure_reason = "migration_dir_missing"
        state.outcome = "health_failed"
        log.error("deployer.migrations_dir_error", error=str(exc))
        return state
    # Stash the (filename, sql) pairs for the gate card + apply step.
    state.scratch["pending_migrations"] = migrations
    if not migrations:
        log.info("deployer.no_migrations_found", deployment_id=str(state.deployment_id))
        state.migration_ok = True  # nothing to run — gate is skipped
    return state


def route_after_discover(state: DeployerState) -> str:
    """Discover error → emit; no pending migrations → provision (skip gate);
    pending migrations → G3 gate."""
    if state.outcome == "health_failed":
        return "emit"
    if not state.scratch.get("pending_migrations"):
        return "provision_cloud_run"
    return "hitl_gate"


@traced_node("hitl_gate")
def hitl_gate_node(state: DeployerState) -> DeployerState:
    """G3 — migration approval. Founder reviews the actual SQL before it runs.

    Reached only when pending migrations exist. The backup is already taken, so
    the card states that explicitly. Fail-closed: no approval → nothing migrates.
    """
    migrations = state.scratch.get("pending_migrations", [])
    files = [f for f, _ in migrations]
    body = (
        f"⚠️ **{len(migrations)} pending DB migration(s)** will run against production.\n"
        f"Backup already taken: `{state.backup_ref}` (PITR-restorable).\n\n"
        + "\n\n".join(
            f"### `{fname}`\n```sql\n{sql[:1500]}\n```" for fname, sql in migrations
        )
    )
    item = ReviewItem(
        key=state.session_id or "migrations",
        subject=f"Approve {len(migrations)} DB migration(s) — deployment {state.deployment_id}",
        context_md=body,
        draft_ref={"deployment_id": str(state.deployment_id), "migration_files": files},
    )
    decisions = gate(
        Gate.DEPLOY_OR_INFRA,
        [item],
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        correlation_id=state.correlation_id,
    )
    state.migrations_approved = decisions.get(item.key) == "approved"
    if not state.migrations_approved:
        state.migration_ok = False
        state.failure_reason = "migration_rejected"
        state.outcome = "health_failed"
        log.info("deployer.migrations_rejected", deployment_id=str(state.deployment_id))
    return state


def route_after_gate(state: DeployerState) -> str:
    return "apply_migrations" if state.migrations_approved else "emit"


@traced_node("apply_migrations")
def apply_migrations_node(state: DeployerState) -> DeployerState:
    """Guardrail #2: provision never runs if migrations fail. Runs only the
    founder-approved pending migrations."""
    migrations = state.scratch.get("pending_migrations", [])
    for filename, sql in migrations:
        ok, error = run_migrations(state.deployment_id, sql)
        if not ok:
            state.migration_ok = False
            state.migration_error = f"{filename}: {error}"
            state.failure_reason = "migration_error"
            state.outcome = "health_failed"
            log.error(
                "deployer.migrations_failed",
                deployment_id=str(state.deployment_id),
                file=filename,
                error=error,
            )
            return state
    state.migration_ok = True
    return state


def route_after_migrations(state: DeployerState) -> str:
    """Short-circuit to emit if migrations failed (guardrail #2)."""
    return "emit" if not state.migration_ok else "provision_cloud_run"


# ─── Node 3: Provision ──────────────────────────────────────────────────────


@traced_node("provision_cloud_run")
def provision_cloud_run_node(state: DeployerState) -> DeployerState:
    service_id = settings.railway_service_id
    ok, live_url = provision_railway(service_id, state.deployment_id)
    state.provision_ok = ok
    state.live_url = live_url
    if not ok:
        state.failure_reason = "provision_error"
        state.outcome = "health_failed"
        log.error(
            "deployer.provision_failed",
            deployment_id=str(state.deployment_id),
        )
    return state


def route_after_provision(state: DeployerState) -> str:
    return "emit" if not state.provision_ok else "update_dns"


# ─── Node 4: DNS / health URL resolution ────────────────────────────────────


@traced_node("update_dns")
def update_dns_node(state: DeployerState) -> DeployerState:
    """Resolve the health URL from the live deployment URL.

    In Railway the domain is returned by provision_railway. In a
    Cloud Run setup this node would update a DNS CNAME via the GCP API.
    For now it composes the health_url from live_url + the health path.
    """
    base = (state.live_url or "").rstrip("/")
    state.health_url = base + _HEALTH_PATH if base else None
    if not state.health_url:
        log.warning("deployer.no_health_url", deployment_id=str(state.deployment_id))
    return state


# ─── Node 5: Smoke test (trigger+poll loop) ─────────────────────────────────

_SMOKE_MAX_ATTEMPTS = 5
_SMOKE_BACKOFF_S = 15      # wait between attempts (mirrors old _RETRY_BACKOFF_S in tools.py)
_SMOKE_REQUEST_TIMEOUT_S = 15.0  # per-request httpx timeout


@traced_node("smoke_attempt")
def smoke_attempt_node(state: DeployerState) -> DeployerState:
    """Single smoke-test attempt. Graph loops back here until done or max attempts."""
    if not state.health_url:
        state.smoke_ok = False
        state.smoke_done = True
        state.failure_reason = "smoke_timeout"
        state.outcome = "health_failed"
        return state

    # Sleep before retries (not before first attempt)
    if state.smoke_attempts > 0:
        time.sleep(_SMOKE_BACKOFF_S)

    ok, status_code, should_retry = smoke_test_once(state.health_url, _SMOKE_REQUEST_TIMEOUT_S)
    new_attempts = state.smoke_attempts + 1

    if ok:
        state.smoke_ok = True
        state.smoke_done = True
        state.smoke_attempts = new_attempts
        state.smoke_status_code = 200
        return state

    state.smoke_ok = False
    state.smoke_status_code = status_code
    state.smoke_attempts = new_attempts

    if not should_retry or new_attempts >= _SMOKE_MAX_ATTEMPTS:
        state.smoke_done = True
        state.failure_reason = (
            "smoke_timeout" if status_code in (0, 502, 503, 504)
            else f"smoke_bad_status_{status_code}"
        )
        state.outcome = "health_failed"
        log.error(
            "deployer.smoke_failed",
            deployment_id=str(state.deployment_id),
            status_code=status_code,
            attempts=new_attempts,
            health_url=state.health_url,
        )
    # If should_retry and budget remains: smoke_done stays False → graph loops
    return state


def route_after_smoke(state: DeployerState) -> str:
    if state.smoke_ok:
        return "emit"
    if state.smoke_done:
        return "rollback"
    return "smoke_attempt"   # loop — try again


# ─── Node 6: Rollback ───────────────────────────────────────────────────────


@traced_node("rollback")
def rollback_node(state: DeployerState) -> DeployerState:
    """Deterministic rollback to the known-good pre-deploy state (NO LLM).

    INVARIANT: restore DB before reverting the container — data integrity first,
    and it avoids a code/schema mismatch (old code against a migrated DB). A smoke
    failure means the deploy never served real traffic (smoke is the first
    request), so restoring to the pre-deploy backup loses no production data.
    """
    state.rollback_attempted = True
    rollback_ok = True
    service_id = settings.railway_service_id

    # 1. If a migration actually applied and we have a backup, restore the DB
    #    first — otherwise reverting the container alone leaves old code against
    #    the new schema.
    if state.migration_ok and state.backup_ref:
        pitr_ok, pitr_err = restore_database_pitr(state.backup_ref, state.deployment_id)
        if not pitr_ok:
            rollback_ok = False
            log.error("deployer.pitr_restore_failed", error=pitr_err)
        else:
            log.info("deployer.pitr_restore_complete", backup_ref=state.backup_ref)

    # 2. Revert the container to the previous good release.
    if state.provision_ok and service_id:
        ok, err = rollback_to_previous(state.deployment_id, service_id)
        if not ok:
            rollback_ok = False
            log.error("deployer.container_rollback_failed", error=err)

    state.rollback_ok = rollback_ok
    state.failure_reason = "rollback_failed" if not rollback_ok else state.failure_reason
    state.outcome = "rollback_ok" if rollback_ok else "rollback_failed"
    return state


# ─── Node 7: Emit ───────────────────────────────────────────────────────────


@traced_node("emit")
def emit_node(state: DeployerState) -> DeployerState:
    """Persist health log and emit the terminal event."""
    # Derive outcome: happy path leaves state.outcome=None (no failure set it).
    outcome = state.outcome or ("confirmed" if state.smoke_ok else "health_failed")

    persist_health_log(
        state.deployment_id,
        backup_ref=state.backup_ref,
        migration_ok=state.migration_ok,
        provision_ok=state.provision_ok,
        smoke_ok=state.smoke_ok,
        rollback_attempted=state.rollback_attempted,
        rollback_ok=state.rollback_ok,
        outcome=outcome,
        failure_reason=state.failure_reason,
    )

    if outcome == "confirmed":
        update_deployment_status(state.deployment_id, "live")
        emit_event(
            EventType.DEPLOYMENT_HEALTH_CONFIRMED,
            source_agent=state.agent_name,
            payload={
                "deployment_id": str(state.deployment_id),
                "client_id": str(state.client_id),
                "health_url": state.health_url or "",
                "smoke_status_code": state.smoke_status_code or 200,
                "backup_ref": state.backup_ref,
            },
            correlation_id=state.correlation_id,
        )
        log.info(
            "deployer.health_confirmed",
            deployment_id=str(state.deployment_id),
            health_url=state.health_url,
        )
    else:
        update_deployment_status(state.deployment_id, "failed")
        emit_event(
            EventType.DEPLOYMENT_HEALTH_FAILED,
            source_agent=state.agent_name,
            payload={
                "deployment_id": str(state.deployment_id),
                "client_id": str(state.client_id),
                "failure_reason": state.failure_reason or "unknown",
                "rollback_ok": state.rollback_ok,
                "backup_ref": state.backup_ref,
            },
            correlation_id=state.correlation_id,
        )
        log.error(
            "deployer.health_failed",
            deployment_id=str(state.deployment_id),
            failure_reason=state.failure_reason,
            rollback_ok=state.rollback_ok,
        )

    return state


# ─── Graph assembly ──────────────────────────────────────────────────────────


def build() -> object:
    g = StateGraph(DeployerState)

    g.add_node("backup_database", backup_database_node)
    g.add_node("discover_migrations", discover_migrations_node)
    g.add_node("hitl_gate", hitl_gate_node)
    g.add_node("apply_migrations", apply_migrations_node)
    g.add_node("provision_cloud_run", provision_cloud_run_node)
    g.add_node("update_dns", update_dns_node)
    g.add_node("smoke_attempt", smoke_attempt_node)
    g.add_node("rollback", rollback_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("backup_database")
    g.add_edge("backup_database", "discover_migrations")

    # After discover: error → emit; no migrations → provision (skip gate);
    # pending migrations → G3 gate.
    g.add_conditional_edges(
        "discover_migrations",
        route_after_discover,
        {"emit": "emit", "provision_cloud_run": "provision_cloud_run", "hitl_gate": "hitl_gate"},
    )

    # G3 gate: approved → apply migrations; rejected → emit (health_failed).
    g.add_conditional_edges(
        "hitl_gate",
        route_after_gate,
        {"apply_migrations": "apply_migrations", "emit": "emit"},
    )

    # Guardrail #2: skip provision if migrations failed
    g.add_conditional_edges(
        "apply_migrations",
        route_after_migrations,
        {"emit": "emit", "provision_cloud_run": "provision_cloud_run"},
    )

    g.add_conditional_edges(
        "provision_cloud_run",
        route_after_provision,
        {"emit": "emit", "update_dns": "update_dns"},
    )

    g.add_edge("update_dns", "smoke_attempt")

    # Guardrail #3: rollback branch on smoke failure; loop back on transient retry
    g.add_conditional_edges(
        "smoke_attempt",
        route_after_smoke,
        {"emit": "emit", "rollback": "rollback", "smoke_attempt": "smoke_attempt"},
    )

    g.add_edge("rollback", "emit")
    g.add_edge("emit", END)

    from omerion_core.runtime.checkpointer import get_checkpointer
    return g.compile(checkpointer=get_checkpointer())
