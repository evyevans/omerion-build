"""Webhook bridges — thin HTTP pass-through to existing Omerion clients.

Called by:
  • Hermes VPS (Claude Agent SDK on Hostinger)
  • Managed Agents on Anthropic Claude Console

All routes require Bearer auth (OMERION_WEBHOOK_TOKEN).  The Gmail send
path uses the OAuth refresh-token credentials already configured for
Railway (GOOGLE_OAUTH_CLIENT_ID / SECRET / REFRESH_TOKEN).
"""
from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from omerion_core.clients.google_client import gmail_service
from omerion_core.inbound.signatures import require_bearer
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.bridges")

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_bearer)],
)


# ── Email ───────────────────────────────────────────────────────────────────

class EmailRequest(BaseModel):
    to: str = Field(..., description="Recipient address")
    subject: str = Field(..., min_length=1, max_length=998)
    body: str = Field(..., min_length=1)
    cc: str | None = None
    reply_to: str | None = None
    html: bool = False


class EmailResponse(BaseModel):
    message_id: str
    thread_id: str


@router.post("/send-email", response_model=EmailResponse)
async def send_email(req: EmailRequest) -> EmailResponse:
    """Send an email from the configured Gmail account (evyevans.ai@gmail.com).

    Hermes and managed agents call this instead of SMTP or himalaya.
    userId='me' means the authenticated refresh-token owner is the sender.
    """
    msg = EmailMessage()
    msg["To"] = req.to
    msg["From"] = "me"
    msg["Subject"] = req.subject
    if req.cc:
        msg["Cc"] = req.cc
    if req.reply_to:
        msg["Reply-To"] = req.reply_to

    if req.html:
        msg.set_content("This message requires an HTML-capable email client.")
        msg.add_alternative(req.body, subtype="html")
    else:
        msg.set_content(req.body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        result = (
            gmail_service()
            .users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
    except Exception as exc:
        log.error("bridge_send_email_failed", to=req.to, error=str(exc))
        raise HTTPException(status_code=502, detail=f"Gmail API error: {exc}") from exc

    log.info("bridge_send_email_ok", to=req.to, message_id=result.get("id"))
    return EmailResponse(
        message_id=result.get("id", ""),
        thread_id=result.get("threadId", ""),
    )


# ── Discord notify ───────────────────────────────────────────────────────────

class DiscordNotifyRequest(BaseModel):
    channel_id: str = Field(..., description="Discord channel snowflake ID")
    content: str = Field(..., min_length=1, max_length=2000)


class DiscordNotifyResponse(BaseModel):
    ok: bool
    detail: str = ""


@router.post("/discord/notify", response_model=DiscordNotifyResponse)
async def discord_notify(req: DiscordNotifyRequest) -> DiscordNotifyResponse:
    """Post a plain-text message to any Discord channel via the bot token.

    Managed agents use this for status updates and completion pings.
    """
    import httpx
    from omerion_core.settings import settings

    bot_token = getattr(settings, "discord_bot_token", None)
    if not bot_token:
        raise HTTPException(status_code=503, detail="DISCORD_BOT_TOKEN not configured")

    url = f"https://discord.com/api/v10/channels/{req.channel_id}/messages"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
            json={"content": req.content},
        )

    if resp.status_code not in (200, 201):
        log.error("bridge_discord_notify_failed", channel=req.channel_id, status=resp.status_code)
        raise HTTPException(status_code=502, detail=f"Discord API {resp.status_code}: {resp.text}")

    log.info("bridge_discord_notify_ok", channel=req.channel_id)
    return DiscordNotifyResponse(ok=True)


# ── LinkedIn DM (stub — send via LinkedIn MCP on Hermes directly) ────────────

# ── R1 TRACK — rd_insights persistence ───────────────────────────────────────

_VALID_IMPACT_TAGS = frozenset({"daam", "capa", "remi", "asap", "internal_os"})
_VALID_PRIORITIES = frozenset({"high", "medium", "low"})


class RdInsightRow(BaseModel):
    source_url: str = Field(..., min_length=8)
    source_type: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    impact_tag: str
    estimated_priority: str
    raw_content: str | None = None
    metadata: dict[str, Any] | None = None


class RdInsightsBulkRequest(BaseModel):
    rows: list[RdInsightRow] = Field(..., min_length=1)
    run_date: str | None = None


class RdInsightsBulkResponse(BaseModel):
    supabase_upserts: int
    duplicates_dropped: int
    errors: list[str]


def _rd_insights_rest_upsert(rows: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    """Upsert rows to rd_insights via PostgREST (idempotent on source_url)."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return 0, 0, ["omerion_supabase_not_configured"]

    url = settings.supabase_url.rstrip("/")
    key = settings.supabase_service_role_key
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates,return=representation",
    }
    endpoint = f"{url}/rest/v1/rd_insights?on_conflict=source_url"

    # Check existing URLs for duplicate accounting (ignore-duplicates returns all rows).
    source_urls = [r["source_url"] for r in rows]
    existing: set[str] = set()
    if source_urls:
        try:
            with httpx.Client(timeout=30.0) as client:
                for src in source_urls:
                    chk = client.get(
                        f"{url}/rest/v1/rd_insights",
                        headers={"apikey": key, "Authorization": f"Bearer {key}"},
                        params={"select": "source_url", "source_url": f"eq.{src}", "limit": 1},
                    )
                    if chk.status_code == 200 and chk.json():
                        existing.add(src)
        except Exception as exc:  # noqa: BLE001
            log.warning("rd_insights_prefetch_failed", error=str(exc))

    errors: list[str] = []
    upserts = 0
    duplicates = 0
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(endpoint, headers=headers, json=rows)
            if resp.status_code not in (200, 201):
                errors.append(f"postgrest_{resp.status_code}: {resp.text[:300]}")
                return 0, len(existing), errors
    except Exception as exc:  # noqa: BLE001
        log.error("rd_insights_bridge_post_failed", error=str(exc))
        return 0, len(existing), [str(exc)]

    for row in rows:
        if row["source_url"] in existing:
            duplicates += 1
        else:
            upserts += 1
    return upserts, duplicates, errors


@router.post("/rd/insights", response_model=RdInsightsBulkResponse)
async def upsert_rd_insights(req: RdInsightsBulkRequest) -> RdInsightsBulkResponse:
    """Persist tagged R1 insights to Supabase on behalf of cloud managed agents.

    Anthropic managed agents call this with OMERION_WEBHOOK_TOKEN so they do not
    need SUPABASE_* secrets in the Console credential vault.
    """
    payload: list[dict[str, Any]] = []
    for row in req.rows:
        if row.impact_tag not in _VALID_IMPACT_TAGS:
            raise HTTPException(
                status_code=400,
                detail=f"invalid impact_tag: {row.impact_tag}",
            )
        if row.estimated_priority not in _VALID_PRIORITIES:
            raise HTTPException(
                status_code=400,
                detail=f"invalid estimated_priority: {row.estimated_priority}",
            )
        meta = dict(row.metadata or {})
        if req.run_date:
            meta.setdefault("run_date", req.run_date)
        payload.append(
            {
                "source_url": row.source_url,
                "source_type": row.source_type,
                "title": row.title,
                "summary": row.summary,
                "impact_tag": row.impact_tag,
                "estimated_priority": row.estimated_priority,
                "raw_content": row.raw_content,
                "metadata": meta,
            }
        )

    upserts, duplicates, errors = _rd_insights_rest_upsert(payload)
    log.info(
        "rd_insights_bridge_ok",
        rows=len(payload),
        supabase_upserts=upserts,
        duplicates_dropped=duplicates,
        errors=len(errors),
    )
    return RdInsightsBulkResponse(
        supabase_upserts=upserts,
        duplicates_dropped=duplicates,
        errors=errors,
    )


@router.post("/linkedin/send-dm")
async def linkedin_send_dm_stub() -> dict:
    """LinkedIn DM send is handled by the LinkedIn MCP directly on Hermes.

    Do not route LinkedIn writes through this bridge — call
    linkedin_queue_dm from the Hermes skill instead.
    """
    return {
        "queued": False,
        "detail": (
            "LinkedIn DM send goes through the LinkedIn MCP on Hermes. "
            "Call linkedin_queue_dm directly from the skill, not via this bridge."
        ),
    }
