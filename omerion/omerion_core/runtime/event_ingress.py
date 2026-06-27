"""Map broker event payloads into agent-specific LangGraph state field names.

The broker (omerion_core/events/broker.py) hands every event-triggered run a
generic envelope:

    inputs = {"event_type": ..., "event_payload": {...}, "event_id": ...}

But each downstream agent's graph state expects domain-specific fields like
`account_ids` (list[UUID]) or `candidate_contact_ids` (list[UUID]). Without a
mapper, cron- and event-triggered runs load empty cohorts and silently no-op.

This module is called once, inside `registry.run_agent_by_name`, before the
graph is invoked — so the same normalization applies to event-, cron-, and
Discord-triggered runs.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from omerion_core.logging import get_logger
from omerion_core.optout import is_opted_out

log = get_logger("omerion.runtime.event_ingress")

# Agents that consume per-contact events expect a list of contact UUIDs in
# `candidate_contact_ids`. The broker emits one event per contact with a scalar
# `contact_id` field, so we wrap it in a singleton list.
_CONTACT_CONSUMERS = {
    "icp-scoring",
    "linkedin-outreach",
    "crm-nurture",
    "offer-matching",
}

# Subset of contact-consumers that perform OUTBOUND, irreversible actions
# (live DMs / emails / proposals). For these, opt-out must be enforced here —
# see the W.A.R.T.T note in map_event_payload_to_state.
_OUTBOUND_CONTACT_CONSUMERS = {
    "linkedin-outreach",
    "crm-nurture",
    "offer-matching",
}


def _coerce_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def map_event_payload_to_state(agent_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Enrich `inputs` with agent-specific state fields derived from event_payload.

    Returns a new dict — does not mutate the caller's dict. If the agent isn't
    event-driven or the payload lacks the expected keys, returns inputs unchanged
    (minus the merge of session_id which the caller already set).
    """
    payload = inputs.get("event_payload") or {}
    if not isinstance(payload, dict):
        return inputs

    out = dict(inputs)

    if agent_name == "lead-scraper":
        raw_ids = payload.get("account_ids") or []
        coerced = [u for u in (_coerce_uuid(x) for x in raw_ids) if u is not None]
        if coerced:
            out["account_ids"] = coerced

    elif agent_name in _CONTACT_CONSUMERS:
        contact_id = _coerce_uuid(payload.get("contact_id"))
        if contact_id is not None:
            # W.A.R.T.T opt-out enforcement (Wave 7.1). The single opt-out guard
            # in the architecture — agent_wrapper._filter_cohort — is BYPASSED on
            # the live event path (broker → execute_run → graph.invoke never calls
            # the wrapper). So a `contact.scored` event for a do_not_contact contact
            # would otherwise flow straight into an outbound sender's draft+send
            # nodes. Enforce opt-out here for outbound senders: a blocked contact
            # yields an empty cohort and the agent no-ops. is_opted_out fails safe
            # (returns True on not-found / DB error).
            if agent_name in _OUTBOUND_CONTACT_CONSUMERS and is_opted_out(str(contact_id)):
                out["candidate_contact_ids"] = []
                log.info(
                    "event_ingress_optout_blocked",
                    agent=agent_name,
                    contact_id=str(contact_id),
                )
            else:
                out["candidate_contact_ids"] = [contact_id]

    elif agent_name == "r2-oss-scout":
        # rd.insight.created from R1 → focus the OSS scout on that signal.
        if payload.get("title"):
            out["insight_title"] = str(payload["title"])
        if payload.get("impact_tag"):
            out["insight_impact_tag"] = str(payload["impact_tag"])

    elif agent_name == "client-comms":
        # DEPLOYMENT_LIVE carries client_id + deployment_id; map to state fields.
        if payload.get("client_id"):
            out["client_id"] = str(payload["client_id"])
        if payload.get("blueprint_id"):
            out["blueprint_id"] = str(payload["blueprint_id"])
        if payload.get("deployment_id"):
            out["deployment_id"] = str(payload["deployment_id"])
        # The trigger_type discriminates which draft template to use.
        # DEPLOYMENT_LIVE → "deploy_confirmation"; other callers set this directly.
        if not out.get("trigger_type"):
            out["trigger_type"] = str(payload.get("trigger_type", "deploy_confirmation"))

    elif agent_name == "factory-intake":
        out["session_id"] = str(payload.get("session_id", ""))
        out["blueprint_id"] = str(payload.get("blueprint_id", ""))

    elif agent_name == "automation-strategist":
        out["client_id"] = str(payload.get("client_id", ""))
        out["blueprint_id"] = str(payload.get("blueprint_id", ""))

    elif agent_name == "spec-architect":
        # automation.blueprint.approved (from automation_strategist) carries
        # blueprint_id + client_id. SpecArchitectState reads both; without this
        # mapping load_blueprint sees no blueprint_id and returns a validation error.
        out["blueprint_id"] = str(payload.get("blueprint_id", ""))
        out["client_id"] = str(payload.get("client_id", ""))

    elif agent_name == "executive-polisher":
        out["client_id"] = str(payload.get("client_id", ""))
        out["blueprint_id"] = str(payload.get("blueprint_id", ""))

    elif agent_name == "diagram-delivery":
        out["client_id"] = str(payload.get("client_id", ""))
        out["blueprint_id"] = str(payload.get("blueprint_id", ""))

    elif agent_name == "factory-rag":
        event_type = inputs.get("event_type", "")
        if event_type == "blueprint.approved":
            out["trigger_type"] = "blueprint_ingest"
            out["source_id"] = str(payload.get("blueprint_id", ""))
        elif event_type == "build.task.failed":
            out["trigger_type"] = "failure_ingest"
            out["source_id"] = str(payload.get("task_id", ""))
        elif event_type == "deployment.health_confirmed":
            out["trigger_type"] = "success_ingest"
            out["source_id"] = str(payload.get("deployment_id", ""))
        # Namespace routing key. Defaults to "general" when the upstream event
        # doesn't carry one (factory_rag.tools._namespace falls back identically).
        out["industry"] = str(payload.get("industry") or "general")

    # 2026-06-21: builder / deployer / qa-tester / outcome-attribution event-ingress
    # branches removed — those agents migrated to the Claude Dev cloud platform.
    elif agent_name in ("compliance-checker", "security-auditor"):
        # These agents are fleet-level scanners — they don't need payload fields
        # injected into state. The entries exist so the mapper is complete and
        # any future payload extension (e.g. client_id scope) has a home.
        pass

    # Discord-triggered runs arrive without an event_payload. The key is
    # "discord_message" via /inbound/discord/route (on_message handler) or
    # "message" via /agents/{name}/run (the /run slash command). Copy
    # whichever is present so any agent's intent-parser node can read it
    # from state — applies to ALL agents, not just lead-scraper.
    msg = inputs.get("discord_message") or inputs.get("message") or ""
    if msg:
        out["discord_message"] = msg

    return out
