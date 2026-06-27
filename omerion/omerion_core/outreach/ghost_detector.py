"""Ghost Detector — daily scan for contacts who never replied.

Runs daily at 07:00 ET via APScheduler. Queries outreach_threads for
contacts who have been outreached but haven't replied past the ghost
threshold. Takes one of three actions per contact:

  re_engage (touch_count < 4, age > threshold):
      Schedule a different-channel follow-up by updating
      reengagement_scheduled_at and logging a re-engagement activity.

  escalate_to_hitl (touch_count >= 4, age < 35 days):
      Create a founder_review_queue row so Evykynn can decide whether
      to send a personal message or archive the contact.

  archive (touch_count >= 5, past escalation window):
      Set contacts.do_not_contact = true, ghost_declared = true,
      schedule re-engagement at +90 days.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from omerion_core.clients.supabase_client import supabase
from omerion_core.events.bus import EventType, emit_event
from omerion_core.hitl.review import create_founder_review_task
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.outreach.ghost_detector")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _fetch_ghost_candidates(threshold_days: int) -> list[dict]:
    """Return outreach_threads rows eligible for ghost processing."""
    cutoff = (_now() - timedelta(days=threshold_days)).isoformat()
    try:
        result = supabase.table("outreach_threads").select(
            "thread_id,contact_id,first_touch_at,last_touch_at,"
            "touch_count_total,touch_count_email,touch_count_linkedin"
        ).eq("response_received", False).eq("ghost_declared", False).lte(
            "last_touch_at", cutoff
        ).gte("touch_count_total", 2).execute()
        return result.data or []
    except Exception as exc:  # noqa: BLE001
        log.error("ghost_detector_fetch_error", error=str(exc))
        return []


def _fetch_contact(contact_id: str) -> dict | None:
    try:
        result = supabase.table("contacts").select(
            "contact_id,first_name,persona,stage,email,do_not_contact"
        ).eq("contact_id", contact_id).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as exc:  # noqa: BLE001
        log.warning("ghost_detector_contact_fetch_error", contact_id=contact_id, error=str(exc))
        return None


def _age_days(thread: dict) -> int:
    """Days since last touch."""
    last_touch = thread.get("last_touch_at")
    if not last_touch:
        return 999
    try:
        dt = datetime.fromisoformat(last_touch.replace("Z", "+00:00"))
        return (_now() - dt).days
    except Exception:  # noqa: BLE001
        return 999


def _mark_ghost_declared(thread_id: str, outcome: str) -> None:
    try:
        supabase.table("outreach_threads").update({
            "ghost_declared": True,
            "ghost_declared_at": _now_iso(),
            "ghost_outcome": outcome,
            "updated_at": _now_iso(),
        }).eq("thread_id", thread_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("ghost_detector_mark_error", thread_id=thread_id, error=str(exc))


def _action_re_engage(thread: dict, contact: dict) -> None:
    """Schedule a different-channel follow-up."""
    contact_id = thread["contact_id"]
    thread_id = thread["thread_id"]

    # Determine which channel to try next
    has_email = thread.get("touch_count_email", 0) > 0
    has_li = thread.get("touch_count_linkedin", 0) > 0
    next_channel = "linkedin" if has_email and not has_li else "email"

    reengagement_at = (_now() + timedelta(days=2)).isoformat()
    try:
        supabase.table("outreach_threads").update({
            "reengagement_scheduled_at": reengagement_at,
            "reengagement_strategy": "switch_channel",
            "ghost_declared": True,
            "ghost_declared_at": _now_iso(),
            "ghost_outcome": "re_engage",
            "updated_at": _now_iso(),
        }).eq("thread_id", thread_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("ghost_detector_reengage_error", thread_id=thread_id, error=str(exc))

    emit_event(
        EventType.OUTREACH_REENGAGED,
        source_agent="ghost_detector",
        payload={
            "contact_id": str(contact_id),
            "next_channel": next_channel,
            "reengagement_at": reengagement_at,
        },
        contact_id=contact_id,
    )
    log.info("ghost_detector_re_engage", contact_id=contact_id, next_channel=next_channel)


def _action_escalate_to_hitl(thread: dict, contact: dict) -> None:
    """Create a HITL review task for the founder to decide."""
    contact_id = thread["contact_id"]
    thread_id = thread["thread_id"]
    first_name = contact.get("first_name", "Contact")
    persona = contact.get("persona", "unknown")

    context_md = (
        f"### Ghost Escalation — {first_name}\n\n"
        f"**Contact ID:** `{contact_id}`\n"
        f"**Persona:** {persona}\n"
        f"**Total touches:** {thread['touch_count_total']}\n"
        f"**Days since last touch:** {_age_days(thread)}\n\n"
        f"This contact received {thread['touch_count_total']} outreach attempts "
        f"across multiple channels with no response. "
        f"Options:\n"
        f"- **Approve** = Mark as ghosted and archive (no more outreach for 90 days)\n"
        f"- **Reject** = Keep in queue for another 14 days before re-evaluating"
    )

    try:
        create_founder_review_task(
            agent_name="ghost_detector",
            session_id="ghost_detector",
            subject=f"Ghost escalation: {first_name} ({persona}) — {thread['touch_count_total']} touches",
            context_md=context_md,
            draft_ref={"kind": "ghost_escalation", "contact_id": str(contact_id)},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("ghost_detector_hitl_error", contact_id=contact_id, error=str(exc))

    emit_event(
        EventType.OUTREACH_GHOSTED,
        source_agent="ghost_detector",
        payload={
            "contact_id": str(contact_id),
            "touch_count": thread["touch_count_total"],
            "age_days": _age_days(thread),
        },
        contact_id=contact_id,
    )
    _mark_ghost_declared(thread_id, "escalate_to_hitl")
    log.info("ghost_detector_hitl_escalated", contact_id=contact_id)


def _action_archive(thread: dict, contact: dict) -> None:
    """Set do_not_contact=true, schedule re-engagement at +90 days."""
    contact_id = thread["contact_id"]
    thread_id = thread["thread_id"]
    now = _now_iso()
    reengagement_at = (_now() + timedelta(days=90)).isoformat()

    try:
        supabase.table("contacts").update({
            "do_not_contact": True,
            "updated_at": now,
        }).eq("contact_id", contact_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("ghost_detector_archive_contact_error", contact_id=contact_id, error=str(exc))

    try:
        supabase.table("outreach_threads").update({
            "ghost_declared": True,
            "ghost_declared_at": now,
            "ghost_outcome": "archive",
            "reengagement_scheduled_at": reengagement_at,
            "reengagement_strategy": "do_not_contact",
            "updated_at": now,
        }).eq("thread_id", thread_id).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("ghost_detector_archive_thread_error", thread_id=thread_id, error=str(exc))

    emit_event(
        EventType.OUTREACH_GHOSTED,
        source_agent="ghost_detector",
        payload={
            "contact_id": str(contact_id),
            "outcome": "archive",
            "reengagement_at": reengagement_at,
        },
        contact_id=contact_id,
    )
    log.info("ghost_detector_archived", contact_id=contact_id)


def _classify_outcome(thread: dict) -> str:
    """Determine ghost outcome from thread state."""
    touch_count = thread.get("touch_count_total", 0)
    age = _age_days(thread)
    if touch_count < 4:
        return "re_engage"
    if touch_count >= 5 and age > 35:
        return "archive"
    return "escalate_to_hitl"


def run_ghost_detector() -> None:
    """Main entry point — called by APScheduler daily at 07:00 ET."""
    cfg = settings.agent("crm_nurture")
    threshold_days = cfg.get("ghost_threshold_days", 21)

    log.info("ghost_detector_run_started", threshold_days=threshold_days)
    candidates = _fetch_ghost_candidates(threshold_days)
    log.info("ghost_detector_candidates_found", count=len(candidates))

    re_engaged = 0
    escalated = 0
    archived = 0

    for thread in candidates:
        contact_id = thread.get("contact_id")
        if not contact_id:
            continue

        contact = _fetch_contact(contact_id)
        if not contact:
            continue

        if contact.get("do_not_contact"):
            _mark_ghost_declared(thread["thread_id"], "already_archived")
            continue

        outcome = _classify_outcome(thread)
        try:
            if outcome == "re_engage":
                _action_re_engage(thread, contact)
                re_engaged += 1
            elif outcome == "escalate_to_hitl":
                _action_escalate_to_hitl(thread, contact)
                escalated += 1
            else:
                _action_archive(thread, contact)
                archived += 1
        except Exception as exc:  # noqa: BLE001
            log.error("ghost_detector_action_error",
                      contact_id=contact_id, outcome=outcome, error=str(exc))

    log.info("ghost_detector_run_complete",
             re_engaged=re_engaged, escalated=escalated, archived=archived)
