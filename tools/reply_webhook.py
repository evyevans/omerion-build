"""
Reply webhook handler — receives inbound email webhooks (SendGrid Inbound Parse),
classifies sentiment with Claude, then routes deterministically.

AI call: sentiment classification only.
Action routing: pure if/else — never AI.

REWIRED: Uses omerion_core Backbone modules (Supabase-native) instead of
Google Sheets API + HTTP endpoint calls.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from omerion_core.clients.supabase_client import supabase
from omerion_core.optout import is_opted_out, set_opted_out
from omerion_core.task_engine import on_positive_reply, on_referral, generate_task
from omerion_core.validation import validate_sentiment
from omerion_core.logging import get_logger

# Deployment logger still works — it's a decorator
from tools.deployment_logger import log_deployment

app = FastAPI()
log = get_logger("omerion.webhook.reply")

_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
_UNKNOWN_LOG = Path("tmp/unknown_senders.jsonl")

_client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY) if _ANTHROPIC_KEY else None


# ── Sentiment classifier (THE ONLY AI CALL) ──────────────────────────────────

@log_deployment(skill_name="analyst_classify", triggered_by="webhook", model="claude-sonnet-4-6")
def classify_sentiment(reply_body: str) -> str:
    """Classify reply sentiment. Returns one of 5 validated values."""
    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=10,
        messages=[{
            "role": "user",
            "content": (
                "Classify the sentiment of this reply to a cold outreach email.\n"
                "Return ONLY one word from: POSITIVE, WARM, NEUTRAL, NEGATIVE, REFERRAL\n\n"
                f"Reply:\n{reply_body[:2000]}"
            ),
        }],
    )
    classify_sentiment._last_usage = response.usage
    raw_label = response.content[0].text.strip().upper()

    # Validate against enum — never trust raw AI output
    result = validate_sentiment(raw_label)
    if not result.valid:
        log.warning("sentiment_validation_failed", raw=raw_label, fallback=result.value)
    return result.value


# ── Contact lookup (Supabase-native) ─────────────────────────────────────────

def _find_contact_by_email(email: str) -> Optional[dict]:
    """Look up contact in Supabase by email."""
    try:
        result = supabase.table("contacts").select(
            "contact_id,first_name,last_name,email,persona,stage,"
            "do_not_contact,fit_score"
        ).eq("email", email.lower()).limit(1).execute()
        return result.data[0] if result.data else None
    except Exception as exc:
        log.error("contact_lookup_error", email=email, error=str(exc))
        return None


# ── Deterministic action router (NOT AI) ─────────────────────────────────────

def _route(sentiment: str, contact: dict, reply_body: str) -> None:
    """Pure if/else routing. Zero AI. Uses Backbone modules directly."""
    cid = contact["contact_id"]
    now = datetime.now(timezone.utc).isoformat()

    if sentiment == "POSITIVE":
        on_positive_reply(cid)
        # Create opportunity in Supabase
        try:
            from uuid import uuid4
            supabase.table("opportunities").insert({
                "opportunity_id": str(uuid4()),
                "contact_id": cid,
                "deal_stage": "Discovery",
                "value": 5000,  # default, adjusted by fit_score later
                "next_step": "Schedule introductory call",
                "created_at": now,
                "updated_at": now,
            }).execute()
        except Exception as exc:
            log.warning("opportunity_create_error", contact_id=cid, error=str(exc))

    elif sentiment == "WARM":
        generate_task(
            contact_id=cid,
            task_type="draft_follow_up",
            description="Warm reply — follow up in 2 weeks",
            due_in_days=14,
        )

    elif sentiment == "NEUTRAL":
        pass  # log only — no action

    elif sentiment == "NEGATIVE":
        set_opted_out(cid, reason="negative_reply")

    elif sentiment == "REFERRAL":
        # Create stub contact for referral
        try:
            from uuid import uuid4
            new_id = str(uuid4())
            supabase.table("contacts").insert({
                "contact_id": new_id,
                "first_name": "Referral",
                "last_name": f"from {contact.get('first_name', 'Unknown')}",
                "source": "referral",
                "stage": "new",
                "do_not_contact": False,
                "created_at": now,
                "updated_at": now,
            }).execute()
            on_referral(new_id, referred_by=contact.get("first_name", "Unknown"))
        except Exception as exc:
            log.warning("referral_create_error", contact_id=cid, error=str(exc))

    # Update outbound_communications with reply
    try:
        supabase.table("outbound_communications").update({
            "replied_at": now,
            "status": "replied",
            "reply_sentiment": sentiment,
            "updated_at": now,
        }).eq("contact_id", cid).eq("status", "sent").order(
            "sent_at", desc=True
        ).limit(1).execute()
    except Exception as exc:
        log.warning("outreach_log_update_error", contact_id=cid, error=str(exc))

    # Update contact replied status + intent score
    try:
        # Base +10 for any reply, adjustments per sentiment
        score_adj = {"POSITIVE": 15, "WARM": 5, "NEUTRAL": 3, "NEGATIVE": 0, "REFERRAL": 3}
        adj = score_adj.get(sentiment, 3)
        supabase.rpc("increment_intent_score", {
            "p_contact_id": cid, "p_amount": adj
        }).execute()
    except Exception:
        # Fallback: direct update if RPC doesn't exist
        try:
            existing = supabase.table("contacts").select("intent_score").eq(
                "contact_id", cid
            ).limit(1).execute()
            if existing.data:
                old = existing.data[0].get("intent_score", 0) or 0
                supabase.table("contacts").update({
                    "intent_score": old + score_adj.get(sentiment, 3),
                    "replied": True,
                    "updated_at": now,
                }).eq("contact_id", cid).execute()
        except Exception as exc:
            log.warning("intent_score_update_error", contact_id=cid, error=str(exc))


# ── Webhook endpoint ─────────────────────────────────────────────────────────

@app.post("/webhook/inbound-email")
async def inbound_email(request: Request):
    form = await request.form()
    sender = form.get("from", "")
    subject = form.get("subject", "")
    body = form.get("text", "") or form.get("html", "")
    timestamp = datetime.now(timezone.utc).isoformat()

    sender_email = sender.split("<")[-1].rstrip(">").strip() if "<" in sender else sender.strip()

    contact = _find_contact_by_email(sender_email)

    if contact is None:
        _UNKNOWN_LOG.parent.mkdir(exist_ok=True)
        with _UNKNOWN_LOG.open("a") as f:
            f.write(json.dumps({"email": sender_email, "subject": subject, "at": timestamp}) + "\n")
        generate_task(
            contact_id="system",
            task_type="manual_review",
            description=f"Unknown sender reply: {sender_email} — Subject: {subject}",
        )
        return JSONResponse({"status": "unknown_sender"})

    # Opt-out guard — check BEFORE doing anything
    if is_opted_out(contact["contact_id"]):
        log.info("reply_from_opted_out_ignored", contact_id=contact["contact_id"])
        return JSONResponse({"status": "opted_out_ignored"})

    sentiment = classify_sentiment(body)
    _route(sentiment, contact, body)
    log.info("reply_processed", contact_id=contact["contact_id"], sentiment=sentiment)

    return JSONResponse({"status": "processed", "sentiment": sentiment})
