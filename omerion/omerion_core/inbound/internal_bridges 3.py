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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from omerion_core.clients.google_client import gmail_service
from omerion_core.inbound.signatures import require_bearer
from omerion_core.logging import get_logger

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
