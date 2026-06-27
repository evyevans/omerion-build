"""
OMERION Backbone — Opt-Out Guard Clauses (A2)
==============================================
Every function that touches a contact MUST call `guard_not_opted_out()`
before proceeding. This module also handles the full opt-out cascade:
cancel sequences, cancel queued comms, emit event, log activity.

Integrates with Supabase tables: contacts, outreach_threads,
nurture_sequences, outbound_communications, founder_review_queue.
"""
from __future__ import annotations

from datetime import datetime, timezone

from omerion_core.clients.supabase_client import supabase
from omerion_core.events.bus import EventType, emit_event
from omerion_core.logging import get_logger

log = get_logger("omerion.backbone.optout")


class OptedOutError(Exception):
    """Raised when an action is attempted on an opted-out contact."""

    def __init__(self, contact_id: str):
        self.contact_id = contact_id
        super().__init__(f"Contact {contact_id} is opted out — action blocked")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_opted_out(contact_id: str) -> bool:
    """Check if a contact has do_not_contact=true.

    Returns True (blocked) if:
    - contact has do_not_contact=true
    - contact not found (fail-safe: block)
    - database error (fail-safe: block)
    """
    try:
        result = supabase.table("contacts").select(
            "contact_id,do_not_contact,stage"
        ).eq("contact_id", contact_id).limit(1).execute()

        if not result.data:
            log.warning("optout_contact_not_found", contact_id=contact_id)
            return True  # fail-safe

        contact = result.data[0]
        return bool(contact.get("do_not_contact", False)) or contact.get("stage") == "do_not_contact"

    except Exception as exc:
        log.error("optout_check_error", contact_id=contact_id, error=str(exc))
        return True  # fail-safe: block on error


def guard_not_opted_out(contact_id: str) -> None:
    """Call before any action on a contact. Raises OptedOutError if blocked."""
    if is_opted_out(contact_id):
        raise OptedOutError(contact_id)


def set_opted_out(contact_id: str, reason: str = "prospect_requested") -> None:
    """Full opt-out cascade for a contact.

    1. Set do_not_contact=true on contacts
    2. Cancel all active nurture_sequences
    3. Cancel all queued outbound_communications
    4. Mark outreach_threads as ghost_declared
    5. Cancel any pending founder_review_queue items
    6. Emit CONTACT_OPTED_OUT event
    """
    now = _now_iso()

    # 1. Update contact record
    try:
        supabase.table("contacts").update({
            "do_not_contact": True,
            "stage": "do_not_contact",
            "opted_out_at": now,
            "opted_out_reason": reason,
            "updated_at": now,
        }).eq("contact_id", contact_id).execute()
        log.info("optout_contact_updated", contact_id=contact_id, reason=reason)
    except Exception as exc:
        log.error("optout_contact_update_error", contact_id=contact_id, error=str(exc))

    # 2. Stop all active nurture sequences
    try:
        supabase.table("nurture_sequences").update({
            "status": "stopped",
            "paused_reason": f"opted_out: {reason}",
            "updated_at": now,
        }).eq("contact_id", contact_id).eq("status", "active").execute()
    except Exception as exc:
        log.warning("optout_nurture_stop_error", contact_id=contact_id, error=str(exc))

    # 3. Cancel all queued outbound communications
    try:
        supabase.table("outbound_communications").update({
            "status": "cancelled",
            "updated_at": now,
        }).eq("contact_id", contact_id).in_("status", [
            "queued_for_sender", "queued", "pending_review"
        ]).execute()
    except Exception as exc:
        log.warning("optout_comms_cancel_error", contact_id=contact_id, error=str(exc))

    # 4. Mark outreach threads
    try:
        supabase.table("outreach_threads").update({
            "ghost_declared": True,
            "ghost_declared_at": now,
            "ghost_outcome": "opted_out",
            "updated_at": now,
        }).eq("contact_id", contact_id).execute()
    except Exception as exc:
        log.warning("optout_thread_update_error", contact_id=contact_id, error=str(exc))

    # 5. Cancel pending review items
    try:
        supabase.table("founder_review_queue").update({
            "status": "auto_rejected",
            "decision_reason": f"Contact opted out: {reason}",
            "decided_at": now,
            "updated_at": now,
        }).eq("contact_id", contact_id).eq("status", "pending").execute()
    except Exception as exc:
        log.warning("optout_review_cancel_error", contact_id=contact_id, error=str(exc))

    # 6. Emit event for any listeners
    try:
        emit_event(
            EventType.OUTREACH_GHOSTED,
            source_agent="backbone_optout",
            payload={
                "contact_id": str(contact_id),
                "reason": reason,
                "action": "full_opted_out_cascade",
            },
            contact_id=contact_id,
        )
    except Exception as exc:
        log.warning("optout_event_error", contact_id=contact_id, error=str(exc))

    log.info("optout_cascade_complete", contact_id=contact_id, reason=reason)
