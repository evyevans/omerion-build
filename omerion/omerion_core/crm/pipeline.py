"""Deal pipeline helpers — stage advancement and snapshot reporting.

Validates stage names against DEAL_STAGES (validation.py), writes to the
clients table, and appends an entry to state_change_log so the audit trail
captures CRM transitions alongside agent run transitions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.validation import DEAL_STAGES

log = get_logger("omerion.crm.pipeline")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def advance_deal_stage(client_slug: str, new_stage: str) -> dict[str, Any]:
    """Move a client to a new pipeline stage.

    Validates the stage name, updates clients.deal_stage and
    deal_stage_updated_at, then appends a row to state_change_log for the
    audit trail.

    Returns the updated client row. Raises ValueError for unknown stages or
    missing clients.
    """
    if new_stage not in DEAL_STAGES:
        raise ValueError(
            f"Unknown deal stage {new_stage!r}. Valid stages: {sorted(DEAL_STAGES)}"
        )

    current_resp = (
        supabase.table("clients")
        .select("client_slug, deal_stage")
        .eq("client_slug", client_slug)
        .limit(1)
        .execute()
    )
    if not current_resp.data:
        raise ValueError(f"Client not found: {client_slug!r}")

    current_stage = current_resp.data[0].get("deal_stage")

    supabase.table("clients").update({
        "deal_stage": new_stage,
        "deal_stage_updated_at": _now_iso(),
    }).eq("client_slug", client_slug).execute()

    log.info(
        "deal_stage_advanced",
        client_slug=client_slug,
        from_stage=current_stage,
        to_stage=new_stage,
    )

    try:
        supabase.table("state_change_log").insert({
            "run_id": None,
            "agent_name": "crm",
            "from_status": current_stage,
            "to_status": new_stage,
            "meta": {"client_slug": client_slug, "domain": "deal_pipeline"},
        }).execute()
    except Exception as _audit_err:  # noqa: BLE001
        log.warning("state_change_log_insert_failed", error=str(_audit_err))

    updated = (
        supabase.table("clients")
        .select("*")
        .eq("client_slug", client_slug)
        .limit(1)
        .execute()
    )
    return updated.data[0] if updated.data else {}


def get_pipeline_snapshot() -> dict[str, int]:
    """Return a count of clients per deal stage.

    Used by the /pipeline Discord command to show the founder a quick
    view of where each client sits in the sales funnel.

    Returns a dict like: {"Discovery": 3, "Proposal": 1, "Closed Won": 2, ...}
    Stages with zero clients are omitted.
    """
    try:
        rows = supabase.table("clients").select("deal_stage").execute().data or []
        snapshot: dict[str, int] = {}
        for row in rows:
            stage = row.get("deal_stage") or "Discovery"
            snapshot[stage] = snapshot.get(stage, 0) + 1
        return snapshot
    except Exception as exc:  # noqa: BLE001
        log.warning("pipeline_snapshot_failed", error=str(exc))
        return {}
