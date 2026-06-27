"""Inbound HITL router.

Flow:
    Founder clicks Approve/Reject in Discord #founder-hitl
        → POST /hitl/resolve (bearer auth)
        → resolve_review(review_id, token, decision)
        → runtime.resume_thread(session_id, payload={decision, notes})
    All in one request; Supabase row update + LangGraph Command(resume=...)
    happen atomically from the caller's point of view.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from omerion_core.hitl.review import get_review, resolve_review
from omerion_core.inbound.rate_limit import limit
from omerion_core.inbound.signatures import require_bearer
from omerion_core.logging import get_logger
from omerion_core.runtime import run_lifecycle
from omerion_core.runtime.run_executor import execute_resume

log = get_logger("omerion.inbound.hitl")

# Bearer-auth alone does not stop a leaked token from flooding the queue.
# 30/min/IP is comfortably above expected human approval rate; below DoS threshold.
router = APIRouter(
    prefix="/hitl",
    tags=["hitl"],
    dependencies=[Depends(require_bearer), Depends(limit("hitl", per_minute=30))],
)


class ResolveBody(BaseModel):
    review_id: str
    token: str
    decision: Literal["approved", "rejected", "edited"]
    decided_by: str | None = None
    notes: str | None = None
    new_body: str | None = None        # populated when decision == "edited"
    source_channel: Literal["discord", "sheets", "other"] = "discord"


class ResolveResponse(BaseModel):
    review_id: str
    decision: str
    thread_resumed: bool
    correlation_id: str | None = Field(default=None)


class PendingItem(BaseModel):
    review_id: str
    agent_name: str
    subject: str
    created_at: str | None = None
    # Tokens are surfaced to bearer-authed callers (Discord bot + dashboard)
    # so they can issue /hitl/resolve without reading Supabase directly.
    approve_token: str | None = None
    reject_token: str | None = None


@router.post("/resolve", response_model=ResolveResponse)
def resolve(body: ResolveBody, background_tasks: BackgroundTasks) -> ResolveResponse:
    review = get_review(body.review_id)
    if not review:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "review not found")

    decision_for_storage = body.decision if body.decision != "edited" else "approved"

    try:
        updated = resolve_review(
            body.review_id,
            token=body.token,
            decision=decision_for_storage,
            notes=body.notes,
        )
    except PermissionError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    # `session_id` is the LangGraph thread_id, which by our convention equals
    # the agent_runs.run_id. Schedule the resume on BackgroundTasks so the
    # HTTP ack returns immediately regardless of how long the resumed graph
    # takes to finish.
    thread_id = review.get("session_id")
    resumed = False
    if thread_id:
        try:
            run_lifecycle.transition(thread_id, "running")
        except Exception as exc:  # noqa: BLE001
            log.error("hitl_run_transition_failed", run_id=thread_id, error=str(exc))
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"run {thread_id} could not be transitioned to running — may already be terminal or not found",
            ) from exc

        background_tasks.add_task(
            execute_resume,
            thread_id,
            {
                "decision": body.decision,
                "notes": body.notes,
                "decided_by": body.decided_by,
                "new_body": body.new_body,
                "review_id": body.review_id,
                "source_channel": body.source_channel,
            },
        )
        resumed = True

    log.info(
        "hitl_resolved",
        review_id=body.review_id,
        decision=body.decision,
        thread_resumed=resumed,
        decided_by=body.decided_by,
        source_channel=body.source_channel,
    )
    return ResolveResponse(
        review_id=body.review_id,
        decision=updated.get("decision", decision_for_storage),
        thread_resumed=resumed,
        correlation_id=updated.get("correlation_id"),
    )


@router.get("/pending", response_model=list[PendingItem])
def pending() -> list[PendingItem]:
    """Used by the Discord bot `/pending` command and by the dashboard."""
    from omerion_core.clients.supabase_client import supabase

    resp = (
        supabase.table("founder_review_queue")
        .select("review_id, agent_name, subject, created_at, approve_token, reject_token")
        .eq("decision", "pending")
        .order("created_at", desc=False)
        .execute()
    )
    rows = resp.data or []
    return [PendingItem(**r) for r in rows]
