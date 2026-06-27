"""AUDITOR — Manual Trigger Script.

Run this script to execute a single AUDITOR sweep outside the nightly
scheduler. Useful for:
  - Testing the agent after initial deployment
  - On-demand constitutional audit after a suspicious config change
  - Debugging a specific audit_id

Usage:
    # Nightly-style scan of the past 24h (default)
    cd omerion && python -m scripts.run_auditor

    # Scan a specific window (in hours)
    cd omerion && python -m scripts.run_auditor --hours 48

    # Event-triggered scan for a specific triggering_event_id
    cd omerion && python -m scripts.run_auditor --event-id <uuid>

    # Weekly report mode (same as Monday nightly)
    cd omerion && python -m scripts.run_auditor --weekly-report
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.scripts.run_auditor")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trigger a single AUDITOR sweep")
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Scan window in hours (default: 24). Ignored if --event-id is set.",
    )
    parser.add_argument(
        "--event-id",
        type=str,
        default=None,
        help="Triggering event UUID — run AUDITOR in event-triggered mode.",
    )
    parser.add_argument(
        "--weekly-report",
        action="store_true",
        default=False,
        help="Force weekly report generation regardless of day of week.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    # Validate env before importing the heavy agent graph
    if not settings.supabase_url or not settings.supabase_service_role_key:
        log.error("auditor_run_no_supabase", msg="SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        return 1
    if not settings.anthropic_api_key:
        log.error("auditor_run_no_anthropic", msg="ANTHROPIC_API_KEY must be set")
        return 1

    from agents.auditor.graph import build
    from agents.auditor.state import AuditorState, TriggerMode

    graph = build()

    trigger_mode: TriggerMode = "nightly_cron"
    if args.event_id:
        trigger_mode = "healing_applied"   # Closest applicable mode for manual event trigger

    # Force weekly_report_day to today if --weekly-report flag is set
    today_weekday = date.today().weekday()
    weekly_report_day = today_weekday if args.weekly_report else 0

    initial_state = AuditorState(
        trigger_mode=trigger_mode,
        triggering_event_id=args.event_id,
        scan_window_hours=args.hours,
        weekly_report_day=weekly_report_day,
        session_id="manual_run",
    )

    log.info(
        "auditor_manual_run_starting",
        trigger_mode=trigger_mode,
        hours=args.hours,
        event_id=args.event_id,
        weekly_report=args.weekly_report,
    )

    try:
        config = {"configurable": {"thread_id": f"auditor_manual_{initial_state.run_id}"}}
        result = graph.invoke(initial_state.model_dump(), config=config)

        log.info(
            "auditor_manual_run_complete",
            records_scanned=result.get("records_scanned", 0),
            critical_violations=len(result.get("critical_violations", [])),
            suspicious_flags=len(result.get("suspicious_flags", [])),
            reverts_succeeded=result.get("reverts_succeeded", 0),
            reverts_failed=result.get("reverts_failed", 0),
            cost_usd=result.get("cost_usd", 0.0),
        )
        return 0
    except Exception as exc:
        log.exception("auditor_manual_run_failed", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
