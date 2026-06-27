"""
OMERION Backbone — Task Generation Rules Engine (A5)
=====================================================
Event-driven task creation with dedup, opt-out guards,
and sequence checks. No task can be created for:
  - An opted-out contact
  - A duplicate (same contact + type already open)
  - A sequenced-out contact (4+ touches on all channels)

Integrates with: Supabase tasks/contacts tables, optout module, sequence module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.optout import is_opted_out
from omerion_core.sequence import is_fully_sequenced

log = get_logger("omerion.backbone.tasks")


VALID_TASK_TYPES = frozenset({
    "draft_initial_outreach",
    "draft_follow_up",
    "schedule_call",
    "manual_review",
    "research_company",
    "draft_referral_outreach",
})

VALID_TASK_STATUSES = frozenset({
    "open",
    "in_progress",
    "draft_complete",
    "complete",
    "skipped",
    "cancelled",
})


@dataclass
class TaskResult:
    created: bool
    task_id: str | None
    reason: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_open_task(contact_id: str, task_type: str) -> bool:
    """Check if an open task of the same type already exists for this contact."""
    try:
        result = supabase.table("tasks").select(
            "task_id", count="exact"
        ).eq("contact_id", contact_id).eq(
            "task_type", task_type
        ).in_("status", ["open", "in_progress"]).execute()
        return (result.count or 0) > 0
    except Exception as exc:
        log.warning("task_dedup_check_error",
                    contact_id=contact_id, task_type=task_type, error=str(exc))
        return False  # allow creation on error (better to have a duplicate than miss a task)


def generate_task(
    *,
    contact_id: str,
    task_type: str,
    description: str = "",
    due_in_days: int = 1,
    assigned_to: str = "system",
    metadata: dict | None = None,
) -> TaskResult:
    """Create a task with full guard clause enforcement.

    Guards (in order):
    1. Task type validation
    2. Opt-out check
    3. Sequence check (for outreach tasks only)
    4. Dedup check (no duplicate open tasks)
    """
    # 1. Validate task type
    if task_type not in VALID_TASK_TYPES:
        return TaskResult(
            created=False, task_id=None,
            reason=f"Invalid task_type: '{task_type}' — must be one of {sorted(VALID_TASK_TYPES)}"
        )

    # 2. Opt-out guard
    if is_opted_out(contact_id):
        return TaskResult(
            created=False, task_id=None,
            reason=f"Contact {contact_id} is opted out — task blocked"
        )

    # 3. Sequence check for outreach tasks
    if task_type in ("draft_initial_outreach", "draft_follow_up"):
        if is_fully_sequenced(contact_id):
            return TaskResult(
                created=False, task_id=None,
                reason=f"Contact {contact_id} is fully sequenced on all channels — no more outreach"
            )

    # 4. Dedup check
    if _has_open_task(contact_id, task_type):
        return TaskResult(
            created=False, task_id=None,
            reason=f"Duplicate: open {task_type} task already exists for {contact_id}"
        )

    # All guards passed — create the task
    task_id = str(uuid4())
    now = _now_iso()
    due_date = (datetime.now(timezone.utc) + timedelta(days=due_in_days)).isoformat()

    task_row = {
        "task_id": task_id,
        "contact_id": contact_id,
        "task_type": task_type,
        "status": "open",
        "assigned_to": assigned_to,
        "description": description,
        "due_date": due_date,
        "metadata": metadata or {},
        "created_at": now,
        "updated_at": now,
    }

    try:
        supabase.table("tasks").insert(task_row).execute()
        log.info("task_created", task_id=task_id,
                 contact_id=contact_id, task_type=task_type)
        return TaskResult(created=True, task_id=task_id, reason="Task created")
    except Exception as exc:
        log.error("task_create_error", task_id=task_id, error=str(exc))
        return TaskResult(
            created=False, task_id=None,
            reason=f"Insert failed: {exc}"
        )


def cancel_tasks_for_contact(contact_id: str, reason: str) -> int:
    """Cancel all open/in-progress tasks for a contact. Returns count cancelled."""
    now = _now_iso()
    try:
        result = supabase.table("tasks").update({
            "status": "cancelled",
            "description": reason,
            "updated_at": now,
        }).eq("contact_id", contact_id).in_(
            "status", ["open", "in_progress"]
        ).execute()
        count = len(result.data) if result.data else 0
        log.info("tasks_cancelled", contact_id=contact_id, count=count, reason=reason)
        return count
    except Exception as exc:
        log.error("tasks_cancel_error", contact_id=contact_id, error=str(exc))
        return 0


def complete_task(task_id: str) -> bool:
    """Mark a task as complete."""
    try:
        supabase.table("tasks").update({
            "status": "complete",
            "updated_at": _now_iso(),
        }).eq("task_id", task_id).execute()
        return True
    except Exception as exc:
        log.error("task_complete_error", task_id=task_id, error=str(exc))
        return False


# ── Event-driven task generation rules ────────────────────────────────────────

def on_new_contact(contact_id: str) -> TaskResult:
    """Called after a new contact is created by ingestion.
    Creates 'draft_initial_outreach' task if contact passes all guards.
    """
    return generate_task(
        contact_id=contact_id,
        task_type="draft_initial_outreach",
        description="New contact ingested — draft initial outreach",
        due_in_days=1,
    )


def on_no_reply(contact_id: str, channel: str, days_since_sent: int = 3) -> TaskResult:
    """Called when no reply detected after N days.
    Creates 'draft_follow_up' task if sequence is not complete.
    """
    return generate_task(
        contact_id=contact_id,
        task_type="draft_follow_up",
        description=f"No reply after {days_since_sent} days on {channel}",
        due_in_days=0,  # immediate
        metadata={"channel": channel, "days_since_sent": days_since_sent},
    )


def on_positive_reply(contact_id: str) -> TaskResult:
    """Called by Analyst when reply sentiment = POSITIVE."""
    return generate_task(
        contact_id=contact_id,
        task_type="schedule_call",
        description="Positive reply received — schedule introductory call",
        due_in_days=0,
    )


def on_referral(contact_id: str, referred_by: str) -> TaskResult:
    """Called when Analyst detects a REFERRAL reply."""
    return generate_task(
        contact_id=contact_id,
        task_type="draft_referral_outreach",
        description=f"Referral from {referred_by} — draft outreach",
        due_in_days=1,
        metadata={"referred_by": referred_by},
    )
