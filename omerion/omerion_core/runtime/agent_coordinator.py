"""R3 Coordination Gate — event-driven trigger for R3 Strategic Architect.

Pattern (§6 of architecture audit):
  After R1 and R2 complete each week, mark_agent_complete() is called by each agent's
  terminal graph node. check_r3_gate() fires immediately if both prerequisites are met
  and R3 hasn't already run this week. A Tuesday 10am cron in scheduler.py provides
  a safety net in case mark_agent_complete() was never called (agent crash, etc.).

Table: agent_run_registry (migration 0063_agent_run_registry.sql)
Columns: agent_id, week_number, year, status, completed_at
"""
from __future__ import annotations

from datetime import datetime, timezone

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.runtime.agent_coordinator")

_R1 = "r1-market-tech-watcher"
_R2 = "r2-oss-scout"
_R3 = "r3-strategic-architect"
_PREREQUISITES = (_R1, _R2)


def _current_week_year() -> tuple[int, int]:
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return iso[1], iso[0]  # week, year


def mark_agent_complete(agent_id: str) -> None:
    """Record that `agent_id` has completed its run for the current ISO week.

    Safe to call multiple times — upserts on (agent_id, week, year). After
    updating R1 or R2, automatically checks the R3 gate. Never raises.
    """
    week, year = _current_week_year()
    try:
        supabase.table("agent_run_registry").upsert({
            "agent_id": agent_id,
            "week_number": week,
            "year": year,
            "status": "complete",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="agent_id,week_number,year").execute()
        log.info("agent_marked_complete", agent_id=agent_id, week=week, year=year)
    except Exception as exc:  # noqa: BLE001
        log.warning("agent_coordinator_mark_failed", agent_id=agent_id, error=str(exc))

    if agent_id in _PREREQUISITES:
        try:
            check_r3_gate()
        except Exception as exc:  # noqa: BLE001
            log.warning("agent_coordinator_gate_check_failed", error=str(exc))


def check_r3_gate() -> None:
    """Fire R3 if both R1 and R2 are complete this week and R3 hasn't run yet.

    Called by mark_agent_complete (event path) and by the Tuesday 10am safety-net
    scheduler job (fallback path). Both paths are idempotent.
    """
    week, year = _current_week_year()
    try:
        prereq_result = (
            supabase.table("agent_run_registry")
            .select("agent_id,status")
            .eq("week_number", week)
            .eq("year", year)
            .in_("agent_id", list(_PREREQUISITES))
            .execute()
        )
        statuses = {row["agent_id"]: row["status"] for row in (prereq_result.data or [])}
        if not all(statuses.get(a) == "complete" for a in _PREREQUISITES):
            log.debug("r3_gate_prerequisites_not_met", statuses=statuses, week=week, year=year)
            return

        r3_result = (
            supabase.table("agent_run_registry")
            .select("status")
            .eq("agent_id", _R3)
            .eq("week_number", week)
            .eq("year", year)
            .execute()
        )
        r3_rows = r3_result.data or []
        if r3_rows and r3_rows[0]["status"] in ("running", "complete"):
            log.info("r3_gate_already_ran", status=r3_rows[0]["status"], week=week)
            return

        log.info("r3_gate_triggered", week=week, year=year)
        _trigger_r3()

    except Exception as exc:  # noqa: BLE001
        log.warning("r3_gate_check_failed", error=str(exc))


def _trigger_r3() -> None:
    """Mark R3 as running and launch it through the run lifecycle."""
    week, year = _current_week_year()
    try:
        supabase.table("agent_run_registry").upsert({
            "agent_id": _R3,
            "week_number": week,
            "year": year,
            "status": "running",
        }, on_conflict="agent_id,week_number,year").execute()

        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.run_executor import execute_run
        run = run_lifecycle.create_run(
            agent_name="r3-strategic-architect",
            source_channel="coordinator",
            inputs={},
            triggered_by="coordinator:r1_r2_complete",
        )
        execute_run(run["run_id"])
        log.info("r3_triggered_by_coordinator", run_id=run["run_id"])
    except Exception as exc:  # noqa: BLE001
        log.exception("r3_trigger_failed", error=str(exc))
