"""Response Tracker — polls Gmail for inbound replies to outbound comms.

Runs every 2 hours via APScheduler. On each run:
  1. Fetches unread inbound Gmail messages.
  2. Matches each message to an outbound_communications row via In-Reply-To
     header (primary) or normalized subject (fallback).
  3. On match: sets contacts.replied=true, outbound_communications.replied_at,
     outreach_threads.response_received, emits OUTREACH_REPLIED event.
  4. Marks the Gmail message as read.

Cross-channel stop propagation:
  register_reply_listener() subscribes to OUTREACH_REPLIED events and
  stops all active nurture_sequences + queued outbound_communications for
  the replied contact.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from omerion_core.clients.supabase_client import supabase
from omerion_core.events.bus import EventType, emit_event, subscribe
from omerion_core.logging import get_logger

log = get_logger("omerion.outreach.tracker")

_REPLY_CHANNEL = None  # holds the Supabase realtime channel for cleanup


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_subject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes and whitespace for fuzzy matching."""
    return re.sub(r"^(re|fwd|fw)\s*:\s*", "", subject, flags=re.IGNORECASE).strip().lower()


def _get_gmail():
    from omerion_core.clients.google_client import gmail_service
    return gmail_service()


def _fetch_unread_inbox(service: Any, max_results: int = 50) -> list[dict]:
    """Fetch unread messages from the inbox."""
    try:
        resp = service.users().messages().list(
            userId="me",
            q="in:inbox is:unread",
            maxResults=max_results,
        ).execute()
        return resp.get("messages", [])
    except Exception as exc:  # noqa: BLE001
        log.error("tracker_gmail_list_error", error=str(exc))
        return []


def _get_message_headers(service: Any, msg_id: str) -> dict[str, str]:
    """Fetch message and return relevant headers as a dict."""
    try:
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="metadata",
            metadataHeaders=["In-Reply-To", "References", "Subject", "From"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        return headers
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_gmail_get_error", msg_id=msg_id, error=str(exc))
        return {}


def _mark_as_read(service: Any, msg_id: str) -> None:
    try:
        service.users().messages().modify(
            userId="me", id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_mark_read_error", msg_id=msg_id, error=str(exc))


def _find_comm_by_provider_id(provider_id: str) -> dict | None:
    """Look up outbound_communications row by provider_id (Gmail Message-ID)."""
    if not provider_id:
        return None
    try:
        result = supabase.table("outbound_communications").select(
            "comm_id,contact_id,subject,channel"
        ).eq("provider_id", provider_id).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_comm_lookup_error", provider_id=provider_id, error=str(exc))
        return None


def _find_comm_by_subject(subject: str) -> dict | None:
    """Fallback: find most recent outbound comm with matching normalized subject."""
    normalized = _normalize_subject(subject)
    if not normalized:
        return None
    try:
        result = supabase.table("outbound_communications").select(
            "comm_id,contact_id,subject,channel"
        ).ilike("subject", f"%{normalized[:60]}%").order("sent_at", desc=True).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_subject_lookup_error", subject=subject, error=str(exc))
        return None


def _record_reply(comm: dict) -> None:
    """Update all relevant tables on a detected reply."""
    contact_id = comm["contact_id"]
    comm_id = comm["comm_id"]
    now = _now_iso()

    # Update outbound_communications
    try:
        supabase.table("outbound_communications").update({
            "replied_at": now,
            "status": "replied",
            "updated_at": now,
        }).eq("comm_id", comm_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_comm_update_error", comm_id=comm_id, error=str(exc))

    # Set contacts.replied = true
    try:
        supabase.table("contacts").update({
            "replied": True,
            "updated_at": now,
        }).eq("contact_id", contact_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_contact_update_error", contact_id=contact_id, error=str(exc))

    # Update outreach_threads
    try:
        supabase.table("outreach_threads").update({
            "response_received": True,
            "response_at": now,
            "response_channel": comm.get("channel", "email"),
            "updated_at": now,
        }).eq("contact_id", contact_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_thread_update_error", contact_id=contact_id, error=str(exc))

    # Emit event so cross-channel stop propagation fires
    try:
        emit_event(
            EventType.OUTREACH_REPLIED,
            source_agent="response_tracker",
            payload={
                "contact_id": str(contact_id),
                "comm_id": str(comm_id),
                "channel": comm.get("channel", "email"),
            },
            contact_id=contact_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_emit_error", contact_id=contact_id, error=str(exc))

    log.info("tracker_reply_recorded", contact_id=contact_id, comm_id=comm_id)


def run_response_tracker() -> None:
    """Main entry point — called by APScheduler every 2 hours."""
    log.info("tracker_run_started")
    try:
        service = _get_gmail()
    except Exception as exc:  # noqa: BLE001
        log.error("tracker_gmail_init_error", error=str(exc))
        return

    messages = _fetch_unread_inbox(service)
    log.info("tracker_inbox_fetched", message_count=len(messages))

    matched = 0
    for msg in messages:
        msg_id = msg["id"]
        headers = _get_message_headers(service, msg_id)
        if not headers:
            continue

        # Primary match: In-Reply-To header contains the Gmail Message-ID we stored
        in_reply_to = headers.get("In-Reply-To", "").strip().strip("<>")
        comm = _find_comm_by_provider_id(in_reply_to)

        # Fallback: subject-line matching
        if comm is None:
            subject = headers.get("Subject", "")
            comm = _find_comm_by_subject(subject)

        if comm:
            _record_reply(comm)
            matched += 1

        _mark_as_read(service, msg_id)

    log.info("tracker_run_complete", processed=len(messages), matched=matched)


# ─── Cross-channel stop propagation ──────────────────────────────────────────


def _on_reply_received(event: dict) -> None:
    """Handle OUTREACH_REPLIED event — stop all active sequences for the contact."""
    contact_id = event.get("payload", {}).get("contact_id")
    if not contact_id:
        return

    now = _now_iso()
    log.info("tracker_stop_propagation", contact_id=contact_id)

    # Stop active nurture sequences
    try:
        supabase.table("nurture_sequences").update({
            "status": "stopped",
            "paused_reason": "replied",
            "updated_at": now,
        }).eq("contact_id", contact_id).eq("status", "active").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_stop_nurture_error", contact_id=contact_id, error=str(exc))

    # Cancel queued outbound messages so the LinkedIn sender doesn't fire them
    try:
        supabase.table("outbound_communications").update({
            "status": "cancelled",
            "updated_at": now,
        }).eq("contact_id", contact_id).eq("status", "queued_for_sender").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("tracker_cancel_queued_error", contact_id=contact_id, error=str(exc))


def register_reply_listener() -> None:
    """Subscribe to OUTREACH_REPLIED events for cross-channel stop propagation.

    Called once in main.py lifespan after start_scheduler().
    """
    global _REPLY_CHANNEL
    _REPLY_CHANNEL = subscribe([EventType.OUTREACH_REPLIED], _on_reply_received)
    log.info("tracker_reply_listener_registered")
