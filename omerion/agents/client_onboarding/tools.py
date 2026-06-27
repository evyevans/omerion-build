"""Tools for Client Onboarding.

Skeleton implementations: the LangGraph wiring + HITL gate + Supabase writes
exist; external side-effects (actual schema provisioning, Discord channel
creation, scheduled-report registration) are stubbed with structured log
events and intentionally left as no-ops so the agent can be triggered
end-to-end against a real Supabase without provisioning side effects.

Fill in the bodies marked `# STUB:` once the founder confirms the live
provisioning recipe.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.json_extraction import extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger

from .prompts import (
    INTAKE_PARSE_SYSTEM,
    KICKOFF_BODY_TEMPLATE,
    KICKOFF_SUBJECT,
    PROVISION_PLAN_SYSTEM,
)
from .state import IntakeData, WorkspaceConfig

log = get_logger("omerion.agents.client_onboarding")


def parse_intake(router: ClaudeRouter, raw_message: str) -> IntakeData:
    if not raw_message:
        return IntakeData()
    resp = router.complete(
        system=INTAKE_PARSE_SYSTEM,
        prompt=raw_message,
        tier=Tier.FAST,
        max_tokens=400,
        temperature=0.0,
    )
    data, _ok = extract_json_object(resp["text"])
    return IntakeData(
        client_name=str(data.get("client_name", "")).strip(),
        contact_email=str(data.get("contact_email", "")).strip(),
        industry=str(data.get("industry", "")).strip(),
        vertical=str(data.get("vertical", "")).strip(),
        agreement_url=data.get("agreement_url") or None,
        raw_message=raw_message,
    )


def insert_client_row(intake: IntakeData) -> UUID:
    """Insert into the `clients` table (migration 0009). Returns client_id."""
    row = {
        "name": intake.client_name or "(unnamed)",
        "contact_email": intake.contact_email or None,
        "industry": intake.industry or None,
        "vertical": intake.vertical or None,
        "agreement_url": intake.agreement_url,
        "status": "onboarding",
        "metadata": {"raw_intake": intake.raw_message[:2000]},
    }
    try:
        resp = supabase.table("clients").insert(row).execute()
        rows = resp.data or []
        if rows:
            return UUID(rows[0]["client_id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("client_insert_failed", error=str(exc))
    # Fallback: synthesize a transient client_id so the graph can continue
    # and surface the failure in HITL context for the founder.
    return uuid4()


def draft_workspace_config(router: ClaudeRouter, intake: IntakeData) -> WorkspaceConfig:
    resp = router.complete(
        system=PROVISION_PLAN_SYSTEM,
        prompt=json.dumps(intake.model_dump())[:2000],
        tier=Tier.DEFAULT,
        max_tokens=800,
        temperature=0.2,
    )
    data, _ok = extract_json_object(resp["text"])
    return WorkspaceConfig(
        supabase_schema=str(data.get("supabase_schema", "")).strip().lower(),
        discord_channel_prefix=str(data.get("discord_channel_prefix", "")).strip().lower(),
        enabled_skills=[str(s) for s in (data.get("enabled_skills") or [])],
        persona_overrides={k: str(v) for k, v in (data.get("persona_overrides") or {}).items()},
        notes=str(data.get("notes", "")).strip(),
    )


def provision_workspace(client_id: UUID, config: WorkspaceConfig) -> dict[str, Any]:
    """STUB: idempotently apply the workspace config.

    Today this only logs the intent. Future iterations will:
      - create per-client Supabase rows / RLS policies
      - register Discord channels via discord/create_channels.py logic
      - inject persona_overrides into agents.yaml per client
    """
    log.info(
        "provision_workspace_skeleton",
        client_id=str(client_id),
        schema=config.supabase_schema,
        channel_prefix=config.discord_channel_prefix,
        skills=config.enabled_skills,
    )
    return {"provisioned": False, "reason": "skeleton — see tools.provision_workspace"}


def apply_persona_overrides(client_id: UUID, overrides: dict[str, str]) -> int:
    """STUB: write client-scoped persona copy overrides."""
    if not overrides:
        return 0
    log.info("persona_overrides_skeleton", client_id=str(client_id), count=len(overrides))
    return len(overrides)


def draft_kickoff(intake: IntakeData) -> tuple[str, str]:
    """Draft the client kickoff message (subject, body) BEFORE the founder gate.

    Separated from sending so the founder approves the exact client-facing text
    (G1). send_kickoff then delivers this pre-approved draft.
    """
    first_name = (intake.client_name or "").split(" ")[0] or "there"
    subject = KICKOFF_SUBJECT.format(client_name=intake.client_name or "your team")
    body = KICKOFF_BODY_TEMPLATE.format(first_name=first_name)
    return subject, body


def send_kickoff(intake: IntakeData, subject: str = "", body: str = "") -> bool:
    """STUB: send the (founder-approved) kickoff email via Gmail. Logs only today.

    When this becomes a real Gmail send (G1), it delivers the `subject`/`body`
    already approved at the gate — never auto-generated text the founder never saw.
    """
    if not intake.contact_email:
        return False
    if not subject or not body:
        subject, body = draft_kickoff(intake)
    log.info("kickoff_skeleton", to=intake.contact_email, subject=subject, body_chars=len(body))
    return True


def schedule_reporting(client_id: UUID) -> bool:
    """STUB: register a weekly client report job in APScheduler."""
    log.info("schedule_reporting_skeleton", client_id=str(client_id))
    return True
