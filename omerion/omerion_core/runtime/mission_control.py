"""Mission Control — the three numbers Elon said the dashboard should answer.

Outcomes today, errors today, cost today. One query, one dict, no joins
the dashboard has to construct. The view itself lives in migration 0016;
this module is the typed Python entry point.
"""
from __future__ import annotations

from typing import TypedDict

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.mission_control")


class MissionControlSnapshot(TypedDict):
    outcomes_today: int
    errors_today: int
    cost_usd_today: float
    completed_today: int
    in_flight_now: int
    hitl_waiting_now: int


def snapshot() -> MissionControlSnapshot:
    """Return today's three-question snapshot.

    Reads `mission_control_today` view (migration 0016). Returns zeros on any
    failure so the dashboard never shows stale data — it shows fresh-zero,
    which the operator can investigate.
    """
    zero: MissionControlSnapshot = {
        "outcomes_today": 0,
        "errors_today": 0,
        "cost_usd_today": 0.0,
        "completed_today": 0,
        "in_flight_now": 0,
        "hitl_waiting_now": 0,
    }
    try:
        resp = supabase.table("mission_control_today").select("*").limit(1).execute()
    except Exception as exc:  # noqa: BLE001 — dashboard must never crash on read
        log.warning("mission_control_read_failed", error=str(exc), error_class=type(exc).__name__)
        return zero
    rows = resp.data or []
    if not rows:
        return zero
    row = rows[0]
    return {
        "outcomes_today": int(row.get("outcomes_today") or 0),
        "errors_today": int(row.get("errors_today") or 0),
        "cost_usd_today": float(row.get("cost_usd_today") or 0.0),
        "completed_today": int(row.get("completed_today") or 0),
        "in_flight_now": int(row.get("in_flight_now") or 0),
        "hitl_waiting_now": int(row.get("hitl_waiting_now") or 0),
    }
