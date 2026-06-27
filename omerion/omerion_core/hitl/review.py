"""Founder-in-the-Loop (HITL) — uniform pattern used by every agent.

Flow:
  1. agent calls `create_founder_review_task(...)` — row in `founder_review_queue`.
  2. Apps Script reads the queue and shows approve/reject buttons in the CRM sheet.
     Discord webhook also pings #founder-hitl with ✅/❌ buttons.
  3. Founder clicks a button → Apps Script or Discord POSTs to
     `${OMERION_PUBLIC_BASE_URL}/hitl/resolve` (bearer auth).
  4. `omerion_core.inbound.hitl` calls `resolve_review(...)` then
     `runtime.resume_thread(thread_id, payload)` in the same request.
  5. Short-horizon Agent-SDK flows call `wait_for_decision(...)` to poll;
     LangGraph flows pause via `interrupt(...)` and resume via PostgresSaver.
"""
from __future__ import annotations

import secrets
import time
from typing import Any
from uuid import UUID, uuid4

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.hitl")


def _token() -> str:
    return secrets.token_urlsafe(32)


def create_founder_review_task(
    *,
    agent_name: str,
    session_id: str,
    subject: str,
    context_md: str,
    draft_ref: dict[str, Any],
    correlation_id: UUID | str | None = None,
    delegated_to: str | None = None,
    expires_in_hours: int = 48,
) -> dict[str, Any]:
    """Create a HITL review row. Returns {review_id, approve_url, reject_url, correlation_id}."""
    approve_tok = _token()
    reject_tok = _token()
    corr = str(correlation_id) if correlation_id else str(uuid4())

    row = {
        "agent_name": agent_name,
        "session_id": session_id,
        "correlation_id": corr,
        "subject": subject,
        "context_md": context_md,
        "draft_ref": draft_ref,
        "approve_token": approve_tok,
        "reject_token": reject_tok,
        "decision": "pending",
        "delegated_to": delegated_to,
        "expires_at": f"now() + interval '{expires_in_hours} hours'",
    }
    # expires_at raw SQL is computed server-side by default (column default);
    # we omit it here and rely on the DB default.
    row.pop("expires_at")

    resp = supabase.table("founder_review_queue").insert(row).execute()
    review_id = resp.data[0]["review_id"]

    base = (settings.omerion_public_base_url or "").rstrip("/")
    approve_url = (
        f"{base}/hitl/resolve?review_id={review_id}&token={approve_tok}&decision=approved"
        if base else ""
    )
    reject_url = (
        f"{base}/hitl/resolve?review_id={review_id}&token={reject_tok}&decision=rejected"
        if base else ""
    )

    log.info("hitl_review_created", review_id=review_id, agent=agent_name, subject=subject)

    # Sheets remains the durable audit/approval trail; Discord webhook
    # pings the founder's chat channel. A failed ping must never prevent
    # the review from being created.
    try:
        from omerion_core.notifications.hitl import notify_hitl_review
        notify_hitl_review(
            review_id=review_id,
            agent_name=agent_name,
            session_id=session_id,
            subject=subject,
            context_md=context_md,
            approve_url=approve_url,
            reject_url=reject_url,
            correlation_id=corr,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("hitl_notify_skipped", review_id=review_id, error=str(exc))

    return {
        "review_id": review_id,
        "approve_url": approve_url,
        "reject_url": reject_url,
        "correlation_id": corr,
    }


def get_review(review_id: UUID | str) -> dict[str, Any] | None:
    resp = (
        supabase.table("founder_review_queue")
        .select("*")
        .eq("review_id", str(review_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_review_by_session(session_id: str) -> dict[str, Any] | None:
    """Look up the most recent *pending* HITL review for a given session_id.

    Used by the Discord APPROVE/REJECT button adapters which only know the
    session_id embedded in the Discord button's custom_id payload.
    """
    resp = (
        supabase.table("founder_review_queue")
        .select("*")
        .eq("session_id", session_id)
        .eq("decision", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def resolve_review(
    review_id: UUID | str,
    *,
    token: str,
    decision: str,     # 'approved' | 'rejected'
    notes: str | None = None,
) -> dict[str, Any]:
    """Called by the `/hitl/resolve` inbound route after the founder clicks a button."""
    review = get_review(review_id)
    if not review:
        raise ValueError(f"review not found: {review_id}")

    expected = review["approve_token"] if decision == "approved" else review["reject_token"]
    if not secrets.compare_digest(expected, token):
        raise PermissionError("invalid HITL token")

    if review["decision"] != "pending":
        return review  # idempotent

    from datetime import datetime, timezone

    expires_at = review.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                raise PermissionError(f"HITL review {review_id} has expired — re-run the agent to create a new review")
        except (ValueError, AttributeError):
            pass  # unparseable expires_at — allow through
    supabase.table("founder_review_queue").update({
        "decision": decision,
        "decision_notes": notes,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }).eq("review_id", str(review_id)).execute()

    log.info("hitl_review_resolved", review_id=str(review_id), decision=decision)
    return get_review(review_id) or {}


def get_pending_count_by_session(session_id: str) -> int:
    """Return how many reviews for this session are still pending."""
    resp = (
        supabase.table("founder_review_queue")
        .select("review_id", count="exact", head=True)
        .eq("session_id", session_id)
        .eq("decision", "pending")
        .execute()
    )
    return resp.count or 0


def get_all_reviews_by_session(session_id: str) -> list[dict[str, Any]]:
    """Return all reviews for a session (any decision state)."""
    resp = (
        supabase.table("founder_review_queue")
        .select("review_id,decision,decision_notes")
        .eq("session_id", session_id)
        .execute()
    )
    return resp.data or []


def wait_for_decision(
    review_id: UUID | str,
    *,
    poll_seconds: float = 5.0,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    """Block until a review row's decision is non-pending, or timeout.

    For long waits, prefer LangGraph `interrupt(...)` with PostgresSaver so the
    process can restart without losing the pending review.
    """
    start = time.time()
    while time.time() - start < timeout_seconds:
        row = get_review(review_id)
        if row and row["decision"] != "pending":
            return row
        time.sleep(poll_seconds)
    raise TimeoutError(f"HITL review {review_id} timed out after {timeout_seconds}s")
