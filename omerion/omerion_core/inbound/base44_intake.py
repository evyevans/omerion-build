"""Public website-facing API — Agentic Factory intake, waitlist, contact, video URLs.

All routes here are unauthenticated (public website), rate-limited, and
designed to be called from the Omerion agency website hosted on Vercel.

Routes registered:
  POST /webhooks/base44/intake              — free diagnosis form submission
  GET  /webhooks/base44/intake/status/{id} — diagnosis status polling
  POST /webhooks/base44/confirm            — post-payment blueprint confirm
  POST /webhooks/base44/regenerate         — request blueprint regeneration
  POST /api/waitlist                        — email waitlist capture
  POST /api/contact                         — contact form (sends email)
  GET  /api/videos/{agent_key}             — video URL lookup for VideoPlayer
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import anthropic
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, EmailStr, Field

from omerion_core.clients.google_client import gmail_service
from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.intake")

router = APIRouter(tags=["website"])

_anthropic = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _generate_diagnosis(form: dict[str, Any]) -> dict[str, Any]:
    """Call Claude Haiku to produce a quick operational diagnosis."""
    prompt = f"""You are an AI automation consultant. Based on this real estate professional's data, generate an operational diagnosis.

Business: {form.get('business_name', 'Unknown')}
Role: {form.get('industry', 'Unknown')}
Process to automate: {form.get('process_to_automate', '')}
Pain points: {form.get('pain_points', '')}
Current tools: {form.get('current_tools', '')}
Team size: {form.get('team_size', '')}
Leads/month: {form.get('leads_per_month', 100)}
Avg response time: {form.get('avg_response_time_hours', 24)}h
Manual process %: {form.get('pct_manual_processes', 60)}%
Avg deal value: ${form.get('avg_deal_value', 50000):,}

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{{
  "processes": <integer: number of distinct automatable processes identified, 3-8>,
  "hrs_wk": <integer: hours per week saved by automation, 5-40>,
  "roi_mo": <integer: estimated monthly ROI in USD based on deal value and lead volume>,
  "opportunities": [
    "<specific automation opportunity 1 for this business>",
    "<specific automation opportunity 2>",
    "<specific automation opportunity 3>",
    "<specific automation opportunity 4>"
  ]
}}"""

    try:
        msg = await _anthropic.messages.create(
            model=settings.claude_model_haiku,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = msg.content[0].text.strip()
        return json.loads(text)
    except Exception as exc:
        log.warning("diagnosis_generation_failed", error=str(exc))
        # Fallback: compute simple heuristic diagnosis
        leads = int(form.get("leads_per_month", 100))
        resp_hrs = int(form.get("avg_response_time_hours", 24))
        manual_pct = int(form.get("pct_manual_processes", 60))
        deal_val = int(form.get("avg_deal_value", 50000))
        hrs_wk = max(5, min(40, round((manual_pct / 100) * 25 + (resp_hrs / 72) * 15)))
        roi_mo = round(leads * 0.05 * (resp_hrs / 24) * 0.3 * deal_val)
        return {
            "processes": max(3, min(8, round(manual_pct / 10))),
            "hrs_wk": hrs_wk,
            "roi_mo": roi_mo,
            "opportunities": [
                "Lead intake and qualification can be fully automated, eliminating manual data entry",
                f"Your {resp_hrs}h average response time can be reduced to under 5 minutes with AI agents",
                "Follow-up sequences can run on autopilot, preventing deals from going cold",
                "Reporting and pipeline visibility can be automated with daily digest summaries",
            ],
        }


async def _run_diagnosis_background(session_id: str, form: dict[str, Any]) -> None:
    """Background task: generate diagnosis, then write results to Supabase."""
    diagnosis = await _generate_diagnosis(form)
    try:
        supabase.table("blueprint_requests").update({
            "diagnosis_data": diagnosis,
            "status": "completed",
            "updated_at": _now_iso(),
        }).eq("session_id", session_id).execute()
        log.info("diagnosis_stored", session_id=session_id)

        # Auto-email the requester
        email = form.get("email")
        name = form.get("client_name", "there")
        business = form.get("business_name", "your business")
        if email:
            try:
                import base64
                from email.message import EmailMessage
                msg = EmailMessage()
                msg["To"] = email
                msg["From"] = "me"
                msg["Subject"] = f"Your Omerion Diagnosis is Ready — {business}"
                msg.set_content(
                    f"Hi {name},\n\nYour operational diagnosis for {business} is ready.\n\n"
                    f"We identified {diagnosis.get('processes', 0)} automation opportunities that could save "
                    f"approximately {diagnosis.get('hrs_wk', 0)} hours per week.\n\n"
                    f"Return to your diagnosis page to view the full report and book a strategy call.\n\n"
                    f"The Omerion Team\nhttps://omerion.io"
                )
                raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
                gmail_service().users().messages().send(userId="me", body={"raw": raw}).execute()
                log.info("diagnosis_email_sent", to=email)
            except Exception as email_exc:
                log.warning("diagnosis_email_failed", to=email, error=str(email_exc))
    except Exception as exc:
        log.error("diagnosis_store_failed", session_id=session_id, error=str(exc))


# ── Agentic Factory intake ─────────────────────────────────────────────────────

class IntakeRequest(BaseModel):
    business_name: str = Field(..., min_length=1, max_length=200)
    client_name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=254)
    website_url: str | None = Field(default=None, max_length=500)
    industry: str = Field(..., min_length=1, max_length=100)
    process_to_automate: str = Field(..., min_length=10, max_length=2000)
    pain_points: str | None = Field(default=None, max_length=2000)
    current_tools: str | None = Field(default=None, max_length=500)
    team_size: str | None = Field(default=None, max_length=50)
    timeline: str | None = Field(default=None, max_length=100)
    avg_deal_value: int = Field(default=50000, ge=0)
    leads_per_month: int = Field(default=100, ge=0)
    avg_response_time_hours: int = Field(default=24, ge=0)
    pct_manual_processes: int = Field(default=60, ge=0, le=100)
    test_mode: bool = False


class IntakeResponse(BaseModel):
    session_id: str
    redirect_url: str | None = None


@router.post("/webhooks/base44/intake", response_model=IntakeResponse)
async def intake(req: IntakeRequest, background_tasks: BackgroundTasks) -> IntakeResponse:
    session_id = str(uuid.uuid4())
    form = req.model_dump(exclude={"test_mode"})

    try:
        supabase.table("blueprint_requests").insert({
            "session_id": session_id,
            "email": req.email,
            "client_name": req.client_name,
            "business_name": req.business_name,
            "form_data": form,
            "status": "pending",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }).execute()
    except Exception as exc:
        log.error("intake_db_insert_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to create request") from exc

    background_tasks.add_task(_run_diagnosis_background, session_id, form)
    log.info("intake_accepted", session_id=session_id, business=req.business_name)
    return IntakeResponse(session_id=session_id)


# ── Status polling ─────────────────────────────────────────────────────────────

class StatusResponse(BaseModel):
    status: str
    processes: int | None = None
    hrs_wk: int | None = None
    roi_mo: int | None = None
    opportunities: list[str] | None = None
    blueprint_html: str | None = None


@router.get("/webhooks/base44/intake/status/{session_id}", response_model=StatusResponse)
async def intake_status(session_id: str) -> StatusResponse:
    try:
        result = (
            supabase.table("blueprint_requests")
            .select("status,diagnosis_data,blueprint_html")
            .eq("session_id", session_id)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        log.error("status_fetch_failed", session_id=session_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to fetch status") from exc

    if not result.data:
        raise HTTPException(status_code=404, detail="Session not found")

    row = result.data
    diag = row.get("diagnosis_data") or {}
    return StatusResponse(
        status=row["status"],
        processes=diag.get("processes"),
        hrs_wk=diag.get("hrs_wk"),
        roi_mo=diag.get("roi_mo"),
        opportunities=diag.get("opportunities"),
        blueprint_html=row.get("blueprint_html"),
    )


# ── Blueprint confirm / regenerate ─────────────────────────────────────────────

class ConfirmRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    record_id: str | None = None


@router.post("/webhooks/base44/confirm")
async def confirm(req: ConfirmRequest) -> dict[str, str]:
    try:
        supabase.table("blueprint_requests").update({
            "status": "approved",
            "updated_at": _now_iso(),
        }).eq("session_id", req.session_id).execute()
    except Exception as exc:
        log.error("confirm_failed", session_id=req.session_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to confirm blueprint") from exc
    log.info("blueprint_confirmed", session_id=req.session_id)
    return {"ok": "true"}


@router.post("/webhooks/base44/regenerate")
async def regenerate(req: ConfirmRequest, background_tasks: BackgroundTasks) -> dict[str, str]:
    try:
        row = (
            supabase.table("blueprint_requests")
            .select("form_data")
            .eq("session_id", req.session_id)
            .maybe_single()
            .execute()
        )
        if not row.data:
            raise HTTPException(status_code=404, detail="Session not found")
        form = row.data.get("form_data", {})

        supabase.table("blueprint_requests").update({
            "status": "pending",
            "blueprint_html": None,
            "diagnosis_data": None,
            "updated_at": _now_iso(),
        }).eq("session_id", req.session_id).execute()
    except HTTPException:
        raise
    except Exception as exc:
        log.error("regenerate_failed", session_id=req.session_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to trigger regeneration") from exc

    background_tasks.add_task(_run_diagnosis_background, req.session_id, form)
    log.info("blueprint_regenerating", session_id=req.session_id)
    return {"ok": "true"}


# ── Waitlist ───────────────────────────────────────────────────────────────────

class WaitlistRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    source: str = Field(default="hero_waitlist", max_length=100)


@router.post("/api/waitlist")
async def waitlist(req: WaitlistRequest) -> dict[str, str]:
    try:
        supabase.table("waitlist_entries").upsert(
            {"email": req.email, "source": req.source},
            on_conflict="email",
            ignore_duplicates=True,
        ).execute()
    except Exception as exc:
        log.error("waitlist_insert_failed", email=req.email, error=str(exc))
        raise HTTPException(status_code=500, detail="Failed to save waitlist entry") from exc
    log.info("waitlist_entry_added", email=req.email)
    return {"ok": "true"}


# ── Contact form ───────────────────────────────────────────────────────────────

class ContactRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    email: str = Field(..., min_length=3, max_length=254)
    message: str = Field(..., min_length=1, max_length=5000)


@router.post("/api/contact")
async def contact(req: ContactRequest) -> dict[str, str]:
    import base64
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["To"] = "omerion.io@gmail.com"
    msg["From"] = "me"
    msg["Subject"] = f"Contact from {req.name or req.email} <{req.email}>"
    msg.set_content(f"Name: {req.name or 'Not provided'}\nEmail: {req.email}\n\n{req.message}")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        gmail_service().users().messages().send(userId="me", body={"raw": raw}).execute()
    except Exception as exc:
        log.error("contact_email_failed", from_email=req.email, error=str(exc))
        raise HTTPException(status_code=502, detail="Failed to send email") from exc
    log.info("contact_email_sent", from_email=req.email)
    return {"ok": "true"}


# ── Video URL lookup ───────────────────────────────────────────────────────────

class VideoResponse(BaseModel):
    url: str | None = None


@router.get("/api/videos/{agent_key}", response_model=VideoResponse)
async def video_url(agent_key: str) -> VideoResponse:
    try:
        result = (
            supabase.table("video_urls")
            .select("url")
            .eq("agent_key", agent_key)
            .maybe_single()
            .execute()
        )
    except Exception as exc:
        log.warning("video_url_fetch_failed", agent_key=agent_key, error=str(exc))
        return VideoResponse(url=None)
    if result.data:
        return VideoResponse(url=result.data["url"])
    return VideoResponse(url=None)
