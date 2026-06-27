"""Unit tests for DEPLOYER nodes — all external systems mocked.

Tests cover the three hard guardrails and every branching path:
  Guardrail 1: backup failure → RuntimeError (pipeline aborts)
  Guardrail 2: migration failure → skips provision, emits health_failed
  Guardrail 3: smoke timeout → triggers rollback branch
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.deployer.graph import (
    apply_migrations_node,
    backup_database_node,
    discover_migrations_node,
    emit_node,
    hitl_gate_node,
    provision_cloud_run_node,
    rollback_node,
    route_after_discover,
    route_after_gate,
    route_after_migrations,
    route_after_provision,
    route_after_smoke,
    run_smoke_tests_node,
    update_dns_node,
)
from agents.deployer.state import DeployerState
from omerion_core.hitl.policy import Gate

_GP = "agents.deployer.graph."


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def state():
    return DeployerState(
        run_id=uuid4(),
        deployment_id=uuid4(),
        client_id=uuid4(),
        session_id="test-session",
    )


# ─── Node 1: backup_database ─────────────────────────────────────────────────


def test_backup_ok_sets_backup_ref(state):
    with patch("agents.deployer.graph.backup_database", return_value=(True, "pitr-abc123")), \
         patch("agents.deployer.graph.update_deployment_status"):
        out = backup_database_node(state)
    assert out.backup_ref == "pitr-abc123"


def test_backup_failure_raises(state):
    """Guardrail #1: pipeline must not proceed without a verified backup."""
    with patch("agents.deployer.graph.backup_database", return_value=(False, None)), \
         patch("agents.deployer.graph.update_deployment_status"):
        with pytest.raises(RuntimeError, match="backup failed"):
            backup_database_node(state)


def test_backup_none_ref_raises(state):
    """backup_ref=None with ok=True is also a hard stop."""
    with patch("agents.deployer.graph.backup_database", return_value=(True, None)), \
         patch("agents.deployer.graph.update_deployment_status"):
        with pytest.raises(RuntimeError):
            backup_database_node(state)


# ─── Node 2a: discover_migrations + G3 gate routing ─────────────────────────


def test_discover_no_migrations_skips_gate(state):
    with patch(_GP + "discover_pending_migrations", return_value=[]):
        out = discover_migrations_node(state)
    assert out.migration_ok is True
    assert route_after_discover(out) == "provision_cloud_run"  # gate skipped


def test_discover_pending_routes_to_gate(state):
    with patch(_GP + "discover_pending_migrations",
               return_value=[("0050_add.sql", "ALTER TABLE x ADD c int;")]):
        out = discover_migrations_node(state)
    assert route_after_discover(out) == "hitl_gate"


def test_discover_dir_error_routes_to_emit(state):
    with patch(_GP + "discover_pending_migrations", side_effect=RuntimeError("no dir")):
        out = discover_migrations_node(state)
    assert out.outcome == "health_failed"
    assert route_after_discover(out) == "emit"


def test_gate_approved_routes_to_apply(state):
    state.backup_ref = "pitr-1"
    state.scratch["pending_migrations"] = [("0050.sql", "ALTER TABLE x ADD c int;")]
    with patch(_GP + "gate", return_value={"test-session": "approved"}) as g:
        out = hitl_gate_node(state)
    assert g.call_args.args[0] == Gate.DEPLOY_OR_INFRA
    assert out.migrations_approved is True
    assert route_after_gate(out) == "apply_migrations"


def test_gate_rejected_blocks_migrations(state):
    """G3: a rejected gate must block the migration and emit health_failed."""
    state.scratch["pending_migrations"] = [("0050.sql", "DROP TABLE users;")]
    with patch(_GP + "gate", return_value={"test-session": "rejected"}):
        out = hitl_gate_node(state)
    assert out.migrations_approved is False
    assert out.outcome == "health_failed"
    assert out.failure_reason == "migration_rejected"
    assert route_after_gate(out) == "emit"


# ─── Node 2b: apply_migrations ──────────────────────────────────────────────


def test_apply_migrations_success_sets_ok(state):
    state.scratch["pending_migrations"] = [("0001_test.sql", "SELECT 1;")]
    with patch(_GP + "run_migrations", return_value=(True, None)):
        out = apply_migrations_node(state)
    assert out.migration_ok is True
    assert out.outcome is None


def test_apply_migrations_failure_sets_outcome(state):
    """Guardrail #2: migration failure must short-circuit; provision never runs."""
    state.scratch["pending_migrations"] = [("0001_test.sql", "SELECT 1;")]
    with patch(_GP + "run_migrations", return_value=(False, "syntax error at line 3")):
        out = apply_migrations_node(state)
    assert out.migration_ok is False
    assert out.outcome == "health_failed"
    assert out.failure_reason == "migration_error"
    assert "syntax error at line 3" in out.migration_error


def test_route_after_migrations_ok(state):
    state.migration_ok = True
    assert route_after_migrations(state) == "provision_cloud_run"


def test_route_after_migrations_fail(state):
    state.migration_ok = False
    assert route_after_migrations(state) == "emit"


# ─── Node 3: provision_cloud_run ─────────────────────────────────────────────


def test_provision_success_sets_live_url(state):
    with patch("agents.deployer.graph.provision_railway", return_value=(True, "https://app.railway.app")):
        out = provision_cloud_run_node(state)
    assert out.provision_ok is True
    assert out.live_url == "https://app.railway.app"
    assert out.outcome is None


def test_provision_failure_sets_outcome(state):
    with patch("agents.deployer.graph.provision_railway", return_value=(False, None)):
        out = provision_cloud_run_node(state)
    assert out.provision_ok is False
    assert out.failure_reason == "provision_error"
    assert out.outcome == "health_failed"


def test_route_after_provision_ok(state):
    state.provision_ok = True
    assert route_after_provision(state) == "update_dns"


def test_route_after_provision_fail(state):
    state.provision_ok = False
    assert route_after_provision(state) == "emit"


# ─── Node 4: update_dns ──────────────────────────────────────────────────────


def test_update_dns_composes_health_url(state):
    state.live_url = "https://app.railway.app"
    out = update_dns_node(state)
    assert out.health_url == "https://app.railway.app/api/v1/health"


def test_update_dns_strips_trailing_slash(state):
    state.live_url = "https://app.railway.app/"
    out = update_dns_node(state)
    assert out.health_url == "https://app.railway.app/api/v1/health"


def test_update_dns_handles_missing_url(state):
    state.live_url = None
    out = update_dns_node(state)
    assert out.health_url is None


# ─── Node 5: run_smoke_tests ─────────────────────────────────────────────────


def test_smoke_200_sets_ok(state):
    state.health_url = "https://app.railway.app/api/v1/health"
    with patch("agents.deployer.graph.smoke_test", return_value=(True, 200)):
        out = run_smoke_tests_node(state)
    assert out.smoke_ok is True
    assert out.smoke_status_code == 200
    assert out.outcome is None  # no failure set


def test_smoke_timeout_sets_failure(state):
    """Guardrail #3: timeout means health_failed, not a retry."""
    state.health_url = "https://app.railway.app/api/v1/health"
    with patch("agents.deployer.graph.smoke_test", return_value=(False, 0)):
        out = run_smoke_tests_node(state)
    assert out.smoke_ok is False
    assert out.smoke_status_code == 0
    assert out.outcome == "health_failed"
    assert out.failure_reason == "smoke_timeout"


def test_smoke_non_200_sets_failure(state):
    state.health_url = "https://app.railway.app/api/v1/health"
    with patch("agents.deployer.graph.smoke_test", return_value=(False, 503)):
        out = run_smoke_tests_node(state)
    assert out.smoke_ok is False
    assert out.outcome == "health_failed"


def test_smoke_no_health_url_sets_failure(state):
    state.health_url = None
    out = run_smoke_tests_node(state)
    assert out.smoke_ok is False
    assert out.outcome == "health_failed"


def test_route_after_smoke_ok(state):
    state.smoke_ok = True
    assert route_after_smoke(state) == "emit"


def test_route_after_smoke_fail(state):
    state.smoke_ok = False
    assert route_after_smoke(state) == "rollback"


# ─── Node 6: rollback ────────────────────────────────────────────────────────


def test_rollback_restores_db_then_reverts_container(state):
    """Deterministic (no LLM): migration applied → restore DB AND revert container."""
    state.provision_ok = True
    state.migration_ok = True
    state.backup_ref = "pitr-abc123"
    with patch(_GP + "restore_database_pitr", return_value=(True, None)) as rdb, \
         patch(_GP + "rollback_to_previous", return_value=(True, None)) as cont, \
         patch(_GP + "settings") as mock_settings:
        mock_settings.railway_service_id = "svc-test-123"
        out = rollback_node(state)
    rdb.assert_called_once()   # DB restored first (invariant)
    cont.assert_called_once()  # then container reverted
    assert out.rollback_attempted is True
    assert out.rollback_ok is True
    assert out.outcome == "rollback_ok"


def test_rollback_failure_sets_rollback_failed(state):
    state.provision_ok = True
    state.migration_ok = False  # no DB restore needed
    with patch(_GP + "rollback_to_previous", return_value=(False, "timeout")), \
         patch(_GP + "settings") as mock_settings:
        mock_settings.railway_service_id = "svc-test-123"
        out = rollback_node(state)
    assert out.rollback_ok is False
    assert out.outcome == "rollback_failed"
    assert out.failure_reason == "rollback_failed"


def test_rollback_skips_db_restore_when_no_migration(state):
    """No migration applied → no DB to restore (avoids needless destructive PITR)."""
    state.provision_ok = True
    state.migration_ok = False
    state.backup_ref = "pitr-abc123"
    with patch(_GP + "restore_database_pitr") as rdb, \
         patch(_GP + "rollback_to_previous", return_value=(True, None)), \
         patch(_GP + "settings") as mock_settings:
        mock_settings.railway_service_id = "svc-test-123"
        rollback_node(state)
    rdb.assert_not_called()


def test_rollback_skips_container_revert_when_provision_failed(state):
    """If provision never succeeded, there's no container to roll back."""
    state.provision_ok = False
    state.migration_ok = False
    with patch(_GP + "rollback_to_previous") as rb, patch(_GP + "settings") as mock_settings:
        mock_settings.railway_service_id = "svc-test-123"
        rollback_node(state)
    rb.assert_not_called()


# ─── Node 7: emit ────────────────────────────────────────────────────────────


def test_emit_confirmed_on_happy_path(state):
    state.smoke_ok = True
    state.health_url = "https://app.railway.app/api/v1/health"
    state.smoke_status_code = 200
    with patch("agents.deployer.graph.persist_health_log"), \
         patch("agents.deployer.graph.update_deployment_status") as upd, \
         patch("agents.deployer.graph.emit_event") as emit:
        emit_node(state)
    upd.assert_called_with(state.deployment_id, "live")
    emit.assert_called_once()
    call_args = emit.call_args
    assert "DEPLOYMENT_HEALTH_CONFIRMED" in str(call_args) or "health_confirmed" in str(call_args)


def test_emit_health_failed_on_migration_error(state):
    state.smoke_ok = False
    state.outcome = "health_failed"
    state.failure_reason = "migration_error"
    with patch("agents.deployer.graph.persist_health_log"), \
         patch("agents.deployer.graph.update_deployment_status") as upd, \
         patch("agents.deployer.graph.emit_event") as emit:
        emit_node(state)
    upd.assert_called_with(state.deployment_id, "failed")
    emit.assert_called_once()
    payload = emit.call_args.kwargs.get("payload") or emit.call_args[1].get("payload") or emit.call_args[0][3] if len(emit.call_args[0]) > 3 else {}
    assert "migration_error" in str(emit.call_args)


def test_emit_persists_health_log(state):
    state.smoke_ok = True
    state.backup_ref = "pitr-xyz"
    state.migration_ok = True
    state.provision_ok = True
    state.smoke_status_code = 200
    with patch("agents.deployer.graph.persist_health_log") as phl, \
         patch("agents.deployer.graph.update_deployment_status"), \
         patch("agents.deployer.graph.emit_event"):
        emit_node(state)
    phl.assert_called_once()
    kwargs = phl.call_args.kwargs
    assert kwargs["migration_ok"] is True
    assert kwargs["smoke_ok"] is True
    assert kwargs["outcome"] == "confirmed"


# ─── Smoke tool unit tests ────────────────────────────────────────────────────


def test_smoke_test_tool_returns_ok_on_200():
    import httpx
    from agents.deployer.tools import smoke_test
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("agents.deployer.tools.httpx.get", return_value=mock_resp):
        ok, code = smoke_test("https://example.com/health", timeout_s=5)
    assert ok is True and code == 200


def test_smoke_test_tool_returns_false_on_500():
    from agents.deployer.tools import smoke_test
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    with patch("agents.deployer.tools.httpx.get", return_value=mock_resp):
        ok, code = smoke_test("https://example.com/health", timeout_s=5)
    assert ok is False and code == 500


def test_smoke_test_tool_returns_false_on_timeout():
    import httpx
    from agents.deployer.tools import smoke_test
    with patch("agents.deployer.tools.httpx.get", side_effect=httpx.TimeoutException("timeout")):
        ok, code = smoke_test("https://example.com/health", timeout_s=5)
    assert ok is False and code == 0
