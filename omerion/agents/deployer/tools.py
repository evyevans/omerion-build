"""Tools for DEPLOYER — Infrastructure Provisioner (Agentic Factory Agent #18).

Each function performs one external side-effect and returns a plain tuple
so graph nodes can update state cleanly without try/except nesting.
All DB writes use the supabase client; all external API calls use httpx.
"""
from __future__ import annotations

import json
import pathlib
import time
from uuid import UUID

import httpx

from omerion_core.clients.supabase_client import supabase
from omerion_core.http import PermanentHTTPError, TransientHTTPError, safe_request
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.agents.deployer")


# ─── Database helpers ──────────────────────────────────────────────────────


def load_deployment(deployment_id: UUID) -> dict:
    resp = (
        supabase.table("deployments")
        .select("deployment_id,client_id,blueprint_id,status,repo_full_name,service_slug")
        .eq("deployment_id", str(deployment_id))
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise LookupError(f"deployment {deployment_id} not found")
    return resp.data[0]


def update_deployment_status(deployment_id: UUID, status: str) -> None:
    supabase.table("deployments").update(
        {"status": status, "updated_at": "now()"}
    ).eq("deployment_id", str(deployment_id)).execute()


def persist_health_log(
    deployment_id: UUID,
    *,
    backup_ref: str | None,
    migration_ok: bool,
    provision_ok: bool,
    smoke_ok: bool,
    rollback_attempted: bool,
    rollback_ok: bool | None,
    outcome: str,
    failure_reason: str | None,
) -> None:
    supabase.table("deployer_health_log").upsert(
        {
            "deployment_id": str(deployment_id),
            "backup_ref": backup_ref,
            "migration_ok": migration_ok,
            "provision_ok": provision_ok,
            "smoke_ok": smoke_ok,
            "rollback_attempted": rollback_attempted,
            "rollback_ok": rollback_ok,
            "outcome": outcome,
            "failure_reason": failure_reason,
        },
        on_conflict="deployment_id",
        ignore_duplicates=True,
    ).execute()


# ─── Supabase Management API ────────────────────────────────────────────────


def _mgmt_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.supabase_management_token}",
        "Content-Type": "application/json",
    }


def backup_database(deployment_id: UUID) -> tuple[bool, str | None]:
    """Trigger a Supabase point-in-time recovery snapshot.

    Returns (ok, backup_ref). backup_ref is a string identifier
    (timestamp or snapshot ID) the rollback tool can reference.
    """
    if not settings.supabase_management_token or not settings.supabase_project_ref:
        log.warning("deployer.backup_skipped", reason="management_token_or_project_ref_missing")
        # Return a synthetic ref so the pipeline can continue in dev/test.
        return True, f"dev-backup:{deployment_id}"

    url = f"https://api.supabase.com/v1/projects/{settings.supabase_project_ref}/database/backups"
    try:
        resp = safe_request(
            "POST", url,
            service="supabase_mgmt",
            headers=_mgmt_headers(),
            timeout=30.0,
            attempts=3,
        )
        data = resp.json()
        backup_ref = data.get("id") or data.get("name") or str(data)
        log.info("deployer.backup_ok", backup_ref=backup_ref)
        return True, str(backup_ref)
    except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
        log.error("deployer.backup_failed", error=str(exc), error_class=type(exc).__name__)
        return False, None


def run_migrations(deployment_id: UUID, sql: str) -> tuple[bool, str | None]:
    """Execute a SQL migration via the Supabase Management API.

    Returns (ok, error_message). On success error_message is None.
    """
    if not settings.supabase_management_token or not settings.supabase_project_ref:
        log.warning("deployer.migration_skipped", reason="management_token_or_project_ref_missing")
        return True, None

    url = f"https://api.supabase.com/v1/projects/{settings.supabase_project_ref}/database/query"
    try:
        safe_request(
            "POST", url,
            service="supabase_mgmt",
            headers=_mgmt_headers(),
            json={"query": sql},
            timeout=60.0,
            attempts=2,  # SQL migrations are not idempotent at the API layer; one retry only
        )
        log.info("deployer.migration_ok", deployment_id=str(deployment_id))
        return True, None
    except PermanentHTTPError as exc:
        log.error("deployer.migration_api_error", status=exc.status, body=exc.body_excerpt)
        return False, exc.body_excerpt
    except (TransientHTTPError, httpx.HTTPError) as exc:
        log.error("deployer.migration_failed", error=str(exc), error_class=type(exc).__name__)
        return False, str(exc)


# ─── Railway API ────────────────────────────────────────────────────────────


def _railway_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.railway_api_token}",
        "Content-Type": "application/json",
    }


def provision_railway(service_id: str, deployment_id: UUID) -> tuple[bool, str | None]:
    """Trigger a Railway redeployment and return (ok, live_url).

    Uses the Railway GraphQL API to redeploy the latest image for the
    configured service.
    """
    if not settings.railway_api_token or not settings.railway_project_id:
        log.warning("deployer.provision_skipped", reason="railway_token_or_project_id_missing")
        return True, f"https://localhost:8000"

    mutation = """
    mutation ServiceInstanceRedeploy($serviceId: String!) {
      serviceInstanceRedeploy(serviceId: $serviceId)
    }
    """
    try:
        resp = safe_request(
            "POST", settings.railway_api_url,
            service="railway",
            headers=_railway_headers(),
            json={"query": mutation, "variables": {"serviceId": service_id}},
            timeout=60.0,
            attempts=3,
        )
        data = resp.json()
        if "errors" in data:
            log.error("deployer.provision_graphql_error", errors=data["errors"])
            return False, None
        live_url = _fetch_railway_domain(service_id)
        log.info("deployer.provision_ok", service_id=service_id, live_url=live_url)
        return True, live_url
    except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
        log.error("deployer.provision_failed", error=str(exc), error_class=type(exc).__name__)
        return False, None


def _fetch_railway_domain(service_id: str) -> str:
    """Resolve the public domain for a Railway service."""
    query = """
    query ServiceDomains($serviceId: String!) {
      service(id: $serviceId) {
        domains { serviceDomains { domain } }
      }
    }
    """
    try:
        resp = safe_request(
            "POST", settings.railway_api_url,
            service="railway",
            headers=_railway_headers(),
            json={"query": query, "variables": {"serviceId": service_id}},
            timeout=15.0,
            attempts=2,
        )
        data = resp.json()
        domains = (
            data.get("data", {})
            .get("service", {})
            .get("domains", {})
            .get("serviceDomains", [])
        )
        if domains:
            return f"https://{domains[0]['domain']}"
    except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
        log.warning("deployer.fetch_domain_failed", service_id=service_id, error=str(exc))
    return ""


def rollback_to_previous(deployment_id: UUID, service_id: str) -> tuple[bool, str | None]:
    """Rollback Railway service to the previous deployment.

    Returns (ok, error). Uses Railway's deployment rollback mutation.
    """
    if not settings.railway_api_token:
        log.warning("deployer.rollback_skipped", reason="railway_token_missing")
        return False, "railway_token_missing"

    mutation = """
    mutation DeploymentRollback($serviceId: String!) {
      deploymentRollback(id: $serviceId)
    }
    """
    try:
        resp = safe_request(
            "POST", settings.railway_api_url,
            service="railway",
            headers=_railway_headers(),
            json={"query": mutation, "variables": {"serviceId": service_id}},
            timeout=60.0,
            attempts=3,
        )
        data = resp.json()
        if "errors" in data:
            log.error("deployer.rollback_graphql_error", errors=data["errors"])
            return False, str(data["errors"])
        log.info("deployer.rollback_ok", deployment_id=str(deployment_id))
        return True, None
    except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
        log.error("deployer.rollback_failed", error=str(exc), error_class=type(exc).__name__)
        return False, str(exc)


# ─── Smoke test ─────────────────────────────────────────────────────────────

_COLD_START_CODES = {502, 503, 504}
_RETRY_BACKOFF_S = 15   # was 10 — Railway cold starts can take 60-90s, 5×15=75s ceiling


def smoke_test(url: str, timeout_s: float = 60.0, max_retries: int = 5) -> tuple[bool, int]:
    """GET health check with cold-start retry.

    Retries on 502/503/504 (Railway cold-start codes) up to max_retries times
    with _RETRY_BACKOFF_S delay between attempts. Any other non-200 or timeout
    is a hard failure with no retry.
    """
    # Why retry on ConnectError/TimeoutException too: a Railway service during cold
    # start can refuse the TCP connection entirely for the first 5-15s before the
    # web process binds the port. Treating that as a hard fail caused false "deploy
    # failed" reports. We now retry the same way we retry 502/503/504.
    last_code = 0
    last_err: str | None = None
    for attempt in range(max_retries):
        try:
            resp = httpx.get(url, timeout=timeout_s, follow_redirects=True)
            if resp.status_code == 200:
                log.info("deployer.smoke_test", url=url, status=200, ok=True, attempt=attempt)
                return True, 200
            last_code = resp.status_code
            if resp.status_code not in _COLD_START_CODES:
                log.error("deployer.smoke_hard_fail", url=url, status=resp.status_code)
                return False, resp.status_code
            log.warning("deployer.smoke_cold_start", url=url, status=resp.status_code, attempt=attempt)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            last_err = type(exc).__name__
            log.warning("deployer.smoke_transient", url=url, error=last_err, attempt=attempt)
        except Exception as exc:  # noqa: BLE001 — bug-class errors should not be swallowed
            log.error("deployer.smoke_error", url=url, error=str(exc), error_class=type(exc).__name__)
            return False, 0
        if attempt < max_retries - 1:
            time.sleep(_RETRY_BACKOFF_S)
    log.error("deployer.smoke_max_retries", url=url, last_code=last_code,
              last_err=last_err, max_retries=max_retries)
    return False, last_code


def smoke_test_once(url: str, per_request_timeout_s: float = 15.0) -> tuple[bool, int, bool]:
    """Single HTTP health-check attempt. Returns (ok, status_code, should_retry).

    should_retry=True for cold-start codes (502/503/504) and connection
    errors — these are transient and worth another attempt.
    should_retry=False for HTTP 200 (success), non-retryable error codes,
    or unexpected exceptions.
    """
    try:
        resp = httpx.get(url, timeout=per_request_timeout_s, follow_redirects=True)
        if resp.status_code == 200:
            log.info("deployer.smoke_once", url=url, status=200, ok=True)
            return True, 200, False
        if resp.status_code in _COLD_START_CODES:
            log.warning("deployer.smoke_once_cold_start", url=url, status=resp.status_code)
            return False, resp.status_code, True
        log.error("deployer.smoke_once_hard_fail", url=url, status=resp.status_code)
        return False, resp.status_code, False
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
        log.warning("deployer.smoke_once_transient", url=url, error=type(exc).__name__)
        return False, 0, True
    except Exception as exc:  # noqa: BLE001
        log.error("deployer.smoke_once_error", url=url, error=str(exc))
        return False, 0, False


# ─── PITR Database Restore ──────────────────────────────────────────────────


_PITR_POLL_INTERVAL_S = 5
_PITR_POLL_MAX_ATTEMPTS = 60  # 5 minutes ceiling


def restore_database_pitr(backup_ref: str, deployment_id: UUID) -> tuple[bool, str | None]:
    """Restore Supabase DB to a PITR backup snapshot via the Management API.

    Polls until the restore reports 'completed' or 'failed' — the Management API
    returns 202 Accepted immediately and performs the restore asynchronously.
    """
    if not settings.supabase_management_token or not settings.supabase_project_ref:
        log.warning("deployer.pitr_skipped", reason="management_credentials_missing")
        return False, "management_credentials_missing"

    base_url = (
        f"https://api.supabase.com/v1/projects/"
        f"{settings.supabase_project_ref}/database/backups/restore"
    )
    try:
        resp = safe_request(
            "POST", base_url,
            service="supabase_mgmt",
            headers=_mgmt_headers(),
            json={"backup_id": backup_ref},
            timeout=60.0,
            attempts=2,
        )
        restore_id = resp.json().get("id")
        if not restore_id:
            # No restore_id means the API returned 200 with immediate success semantics.
            log.info("deployer.pitr_restore_immediate", backup_ref=backup_ref)
            return True, None

        log.info("deployer.pitr_restore_triggered", backup_ref=backup_ref, restore_id=restore_id)
        status_url = f"{base_url}/{restore_id}"

        for attempt in range(_PITR_POLL_MAX_ATTEMPTS):
            time.sleep(_PITR_POLL_INTERVAL_S)
            try:
                status_resp = safe_request(
                    "GET", status_url,
                    service="supabase_mgmt",
                    headers=_mgmt_headers(),
                    timeout=15.0,
                    attempts=2,
                )
                status_data = status_resp.json()
                restore_status = status_data.get("status", "")
                if restore_status == "completed":
                    log.info("deployer.pitr_restore_complete", backup_ref=backup_ref, attempt=attempt)
                    return True, None
                if restore_status == "failed":
                    log.error("deployer.pitr_restore_status_failed", backup_ref=backup_ref)
                    return False, "pitr_restore_reported_failed"
            except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as poll_exc:
                log.warning("deployer.pitr_poll_error", attempt=attempt, error=str(poll_exc))

        log.error(
            "deployer.pitr_restore_timeout",
            backup_ref=backup_ref,
            waited_s=_PITR_POLL_MAX_ATTEMPTS * _PITR_POLL_INTERVAL_S,
        )
        return False, "pitr_restore_timeout"

    except PermanentHTTPError as exc:
        log.error("deployer.pitr_api_error", status=exc.status, body=exc.body_excerpt)
        return False, exc.body_excerpt
    except (TransientHTTPError, httpx.HTTPError) as exc:
        log.error("deployer.pitr_failed", error=str(exc), error_class=type(exc).__name__)
        return False, str(exc)


# ─── Migration Discovery ─────────────────────────────────────────────────────

_DEFAULT_MIGRATIONS_DIR = pathlib.Path(__file__).parents[3] / "infra" / "supabase" / "migrations"


def discover_pending_migrations(
    migrations_dir: str | pathlib.Path | None = None,
) -> list[tuple[str, str]]:
    """Return list of (filename, sql_content) sorted by filename.

    Reads all .sql files from the migrations directory. No dedup — migrations
    must use IF NOT EXISTS guards for idempotency.
    """
    d = pathlib.Path(migrations_dir) if migrations_dir else _DEFAULT_MIGRATIONS_DIR
    if not d.exists():
        raise RuntimeError(
            f"Migrations directory not found: {d}. "
            "Set MIGRATIONS_DIR env var or verify the Docker COPY step copies infra/supabase/migrations."
        )
    files = sorted(d.glob("*.sql"))
    return [(f.name, f.read_text()) for f in files]
