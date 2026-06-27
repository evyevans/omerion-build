"""Activity logger — writes to `activity_log` for the dashboard Execution Stream.

Every run lifecycle transition emits a row so the dashboard can render
live updates via Supabase Realtime. Failures are swallowed — a broken
activity log must never crash a run.

Table schema (existing):
    id          UUID (default gen_random_uuid())
    agent_id    TEXT FK → agents.id  (underscore format: "crm_nurture")
    run_id      TEXT      — optional run identifier
    event_type  TEXT      — run_start | run_complete | error | warning | info
    message     TEXT      — human-readable one-liner
    duration_ms INTEGER   — optional
    cost_usd    FLOAT     — optional
    created_at  TIMESTAMPTZ (default now())

Note: The runtime registry uses kebab-case skill names (e.g. "hq-lead-scraping")
but the agents table stores underscore IDs (e.g. "high_quality_lead_scraping").
The _to_db_id() function handles this mapping.
"""
from __future__ import annotations

from typing import Any

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.activity_logger")

# Kebab-case skill name → agents.id (underscore) mapping.
# Built from the registry registrations + agents table.
_SKILL_TO_DB: dict[str, str] = {
    "crm-nurture":        "crm_nurture",
    "lead-scraper":       "lead_scraper_enricher",
    "icp-scoring":        "icp_scoring",
    "linkedin-outreach":  "linkedin_outreach",
    "hq-lead-scraping":   "high_quality_lead_scraping",
    "offer-matching":     "offer_matching",
    "market-watcher":     "r1_market_tech_watcher",
    "oss-scout":          "r2_oss_scout",
    "strategic-arch":     "r3_strategic_architect",
    "build-orchestrator": "build_orchestrator",
    "outcome-attribution":"outcome_attribution",
    "eval-telemetry":     "r4_evaluation_telemetry",
    "meeting-intel":      "meeting_intelligence",
    "market-mapper":      "market_mapper",
}


def _to_db_id(skill_name: str) -> str:
    """Convert a kebab-case skill name to the agents.id format."""
    if skill_name in _SKILL_TO_DB:
        return _SKILL_TO_DB[skill_name]
    # Fallback: replace hyphens with underscores
    return skill_name.replace("-", "_")


def _safe_insert(row: dict[str, Any]) -> None:
    """Best-effort insert — never raises."""
    try:
        supabase.table("activity_log").insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("activity_log_insert_failed", error=str(exc), row_agent=row.get("agent_id"))


def log_run_start(agent_name: str, run_id: str, *, triggered_by: str | None = None) -> None:
    """Emit when an agent run transitions to 'running'."""
    _safe_insert({
        "agent_id": _to_db_id(agent_name),
        "run_id": run_id,
        "event_type": "run_start",
        "message": f"Run started{f' by {triggered_by}' if triggered_by else ''}",
    })


def log_run_complete(
    agent_name: str,
    run_id: str,
    *,
    duration_ms: int | None = None,
    cost_usd: float | None = None,
    summary: str | None = None,
) -> None:
    """Emit when an agent run completes successfully."""
    msg = summary[:120] if summary else "Run completed"
    row: dict[str, Any] = {
        "agent_id": _to_db_id(agent_name),
        "run_id": run_id,
        "event_type": "run_complete",
        "message": msg,
    }
    if duration_ms is not None:
        row["duration_ms"] = duration_ms
    if cost_usd is not None:
        row["cost_usd"] = round(cost_usd, 6)
    _safe_insert(row)


def log_run_failed(agent_name: str, run_id: str, *, error: str) -> None:
    """Emit when an agent run fails."""
    _safe_insert({
        "agent_id": _to_db_id(agent_name),
        "run_id": run_id,
        "event_type": "error",
        "message": f"Run failed: {error[:200]}",
    })


def log_hitl_waiting(agent_name: str, run_id: str, *, review_id: str | None = None) -> None:
    """Emit when an agent pauses for founder approval."""
    _safe_insert({
        "agent_id": _to_db_id(agent_name),
        "run_id": run_id,
        "event_type": "warning",
        "message": f"Waiting for founder approval{f' (review {review_id[:8]})' if review_id else ''}",
    })


def log_info(agent_name: str, message: str, *, run_id: str | None = None) -> None:
    """Generic info event for the activity stream."""
    row: dict[str, Any] = {
        "agent_id": _to_db_id(agent_name),
        "event_type": "info",
        "message": message[:300],
    }
    if run_id:
        row["run_id"] = run_id
    _safe_insert(row)
