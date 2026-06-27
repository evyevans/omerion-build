"""TDD tests for DEPLOYER tools — no network calls."""
from unittest.mock import MagicMock, patch
from uuid import uuid4


# ─── D1: cold-start retry in smoke_test ───────────────────────────────────────


def test_smoke_test_retries_on_cold_start():
    """smoke_test must retry 503 before declaring failure."""
    cold_response = MagicMock()
    cold_response.status_code = 503

    ok_response = MagicMock()
    ok_response.status_code = 200

    with patch("agents.deployer.tools.httpx.get", side_effect=[cold_response, cold_response, ok_response]) as mock_get, \
         patch("agents.deployer.tools.time.sleep"):
        from agents.deployer.tools import smoke_test
        ok, code = smoke_test("https://example.com/api/v1/health", timeout_s=5.0)

    assert ok is True
    assert code == 200
    assert mock_get.call_count == 3


def test_smoke_test_fails_after_max_retries():
    """smoke_test must give up and return False after 3 consecutive 503s."""
    cold_response = MagicMock()
    cold_response.status_code = 503

    with patch("agents.deployer.tools.httpx.get", return_value=cold_response), \
         patch("agents.deployer.tools.time.sleep"):
        from agents.deployer.tools import smoke_test
        ok, code = smoke_test("https://example.com/api/v1/health", timeout_s=5.0, max_retries=3)

    assert ok is False


def test_smoke_test_passes_immediately_on_200():
    ok_response = MagicMock()
    ok_response.status_code = 200

    with patch("agents.deployer.tools.httpx.get", return_value=ok_response):
        from agents.deployer.tools import smoke_test
        ok, code = smoke_test("https://example.com/api/v1/health")

    assert ok is True
    assert code == 200


# ─── D2: PITR database restore ────────────────────────────────────────────────


def test_restore_database_pitr_calls_management_api():
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.text = ""

    with patch("agents.deployer.tools.httpx.post", return_value=mock_resp), \
         patch("agents.deployer.tools.settings") as mock_settings:
        mock_settings.supabase_management_token = "tok"
        mock_settings.supabase_project_ref = "abc123"

        from agents.deployer.tools import restore_database_pitr
        ok, err = restore_database_pitr(backup_ref="backup-2026-01-01", deployment_id=uuid4())

    assert ok is True
    assert err is None


def test_restore_database_pitr_skips_without_credentials():
    with patch("agents.deployer.tools.settings") as mock_settings:
        mock_settings.supabase_management_token = ""
        mock_settings.supabase_project_ref = ""

        from agents.deployer.tools import restore_database_pitr
        ok, err = restore_database_pitr(backup_ref="bkp", deployment_id=uuid4())

    assert ok is False
    assert err == "management_credentials_missing"


# ─── D3: migration discovery from disk ────────────────────────────────────────


import pathlib
import tempfile


def test_discover_pending_migrations_returns_sorted_sql():
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "0001_first.sql").write_text("CREATE TABLE a (id int);")
        pathlib.Path(d, "0002_second.sql").write_text("CREATE TABLE b (id int);")
        from agents.deployer.tools import discover_pending_migrations
        results = discover_pending_migrations(migrations_dir=d)
    assert len(results) == 2
    assert results[0][0] == "0001_first.sql"
    assert "CREATE TABLE a" in results[0][1]
    assert results[1][0] == "0002_second.sql"


def test_discover_pending_migrations_returns_empty_for_missing_dir():
    from agents.deployer.tools import discover_pending_migrations
    results = discover_pending_migrations(migrations_dir="/nonexistent/path/xyz")
    assert results == []
