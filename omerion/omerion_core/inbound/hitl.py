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
from fastapi.responses import HTMLResponse
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


# ── One-click capability-URL resolve (GET, no bearer) ────────────────────────
# The Discord/Sheets approve/reject links emitted by create_founder_review_task are
# plain hyperlinks: a browser click is a GET with the token in the query string.
# That cannot satisfy the bearer-protected POST /resolve above, so this router
# accepts the click directly. AUTH = the 32-byte single-use token in the URL
# (a capability URL, like an email one-click-approve link); resolve_review() does
# constant-time token comparison, idempotency, and expiry. This router therefore
# deliberately has NO require_bearer dependency.
public_router = APIRouter(
    prefix="/hitl",
    tags=["hitl"],
    dependencies=[Depends(limit("hitl_resolve_click", per_minute=30))],
)


def _resolve_page(title: str, body: str, color: str) -> HTMLResponse:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Omerion HITL</title></head>"
        "<body style='font-family:-apple-system,Segoe UI,sans-serif;background:#0b0b0d;"
        "color:#e7e7ea;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0'>"
        f"<div style='text-align:center;padding:2rem'><div style='font-size:3rem'>{color}</div>"
        f"<h1 style='font-weight:600'>{title}</h1><p style='color:#9a9aa2'>{body}</p>"
        "<p style='color:#6a6a72;font-size:.85rem'>You can close this tab.</p></div></body></html>"
    )
    return HTMLResponse(html)


@public_router.get("/resolve")
def resolve_click(
    review_id: str,
    token: str,
    decision: Literal["approved", "rejected"],
    background_tasks: BackgroundTasks,
) -> HTMLResponse:
    """Founder clicks the Approve/Reject hyperlink in #founder-hitl → lands here.

    Auth is the in-URL token (capability URL). Flips the review decision, promotes
    the linked rd_proposals row (managed agents have no graph to resume), and
    best-effort resumes a LangGraph thread if session_id is a real run_id.
    """
    review = get_review(review_id)
    if not review:
        return _resolve_page("Not found", "This review no longer exists.", "⚠️")

    try:
        updated = resolve_review(review_id, token=token, decision=decision)
    except PermissionError as exc:
        msg = str(exc)
        if "expired" in msg.lower():
            return _resolve_page("Link expired", "Re-run the agent to create a fresh review.", "⏰")
        return _resolve_page("Invalid link", "This approval link is not valid.", "🚫")
    except ValueError:
        return _resolve_page("Not found", "This review no longer exists.", "⚠️")

    already = updated.get("decision") != decision  # resolve_review is idempotent
    final = updated.get("decision", decision)

    # Promote the linked draft. Managed cloud agents (e.g. R3) have no LangGraph
    # thread to resume, so the bridge-created proposal must be advanced here or it
    # would sit at status='submitted' forever and never reach the build backlog.
    draft = review.get("draft_ref") or {}
    if draft.get("table") == "rd_proposals":
        proposal_id = draft.get("proposal_id") or review.get("correlation_id")
        if proposal_id:
            try:
                from datetime import datetime, timezone
                from omerion_core.clients.supabase_client import supabase
                supabase.table("rd_proposals").update({
                    "status": final,
                    "founder_decided_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("proposal_id", str(proposal_id)).eq("status", "submitted").execute()
            except Exception as exc:  # noqa: BLE001
                log.error("hitl_proposal_promote_failed", proposal_id=str(proposal_id), error=str(exc))

    # Best-effort LangGraph resume: only when session_id is a resumable run thread.
    thread_id = review.get("session_id")
    if thread_id and not str(thread_id).startswith("managed:"):
        try:
            run_lifecycle.transition(thread_id, "running")
            background_tasks.add_task(
                execute_resume,
                thread_id,
                {"decision": final, "review_id": review_id, "source_channel": "discord"},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("hitl_click_resume_skipped", run_id=thread_id, error=str(exc))

    log.info("hitl_resolved_via_click", review_id=review_id, decision=final, idempotent=already)
    verb = "Approved" if final == "approved" else "Rejected"
    icon = "✅" if final == "approved" else "❌"
    note = " (already recorded)" if already else ""
    return _resolve_page(f"{verb}{note}", f"“{review.get('subject', '')}”", icon)


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
