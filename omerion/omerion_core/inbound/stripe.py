"""Inbound Stripe billing webhook — the **source of truth** for revenue events.

This route is migrated from the legacy `api/webhooks/router.py:241` to live
alongside the rest of the canonical inbound surface under
`omerion/omerion_core/inbound/`. It is the single deterministic entry point
that may write to `revenue_events` and `invoices`. No agent may produce
revenue records by inference — see operating-laws spec.

Signature verification follows Stripe's `t={timestamp},v1={hmac_sha256}`
scheme on the raw body. A 5-minute timestamp tolerance prevents replay.
Idempotency is enforced at the DB layer via `UNIQUE(stripe_event_id)` on
`revenue_events` (migration 0014/0040). The handler is therefore safe to
call twice — the second insert is a no-op.

Handles:
  * invoice.payment_succeeded       → revenue_events insert + invoices status update
  * checkout.session.completed      → Agentic Factory self-serve pipeline trigger
  * everything else                 → ack-only, logged for observability
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from omerion_core.inbound.rate_limit import limit
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.inbound.stripe")

# Stripe's documented webhook traffic is well under 60/min in steady state.
# The cap exists to bound damage from a misconfigured replay or compromised
# webhook signing secret. Bursts are absorbed by the token-bucket capacity.
router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(limit("stripe", per_minute=60))],
)


class StripeAck(BaseModel):
    received: bool
    event_type: str = ""
    processed: bool = False
    event_id: str = ""


# ─────────────────────────── signature ───────────────────────────

def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> dict[str, Any]:
    """Verify Stripe-Signature HMAC and return the parsed event.

    Stripe signs events as: t={timestamp},v1={hmac_sha256}
    We compute HMAC-SHA256 of "{timestamp}.{raw_payload}" and compare.
    Raises ValueError on bad signature or stale timestamp (>5 min).
    """
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    timestamp_str = parts.get("t", "")
    provided_sig = parts.get("v1", "")
    if not timestamp_str or not provided_sig:
        raise ValueError("malformed Stripe-Signature header")
    try:
        ts = int(timestamp_str)
    except ValueError as exc:
        raise ValueError("invalid timestamp in Stripe-Signature") from exc
    if abs(time.time() - ts) > 300:
        raise ValueError("Stripe webhook timestamp outside 5-minute tolerance")
    signed = f"{timestamp_str}.{payload.decode('utf-8')}"
    expected = hmac.new(
        secret.encode("utf-8"), signed.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, provided_sig):
        raise ValueError("Stripe signature mismatch")
    return json.loads(payload)


# ─────────────────────────── persistence ───────────────────────────

async def _persist_invoice_payment(event: dict[str, Any], event_id: str) -> None:
    """Insert revenue_event + update invoice. Best-effort but logs loudly.

    Idempotency is DB-enforced via UNIQUE(stripe_event_id) — second call is a
    silent no-op on the revenue_events insert. The invoice status update is
    idempotent by nature (PATCH to the same row with the same values).
    """
    inv = event.get("data", {}).get("object", {})
    stripe_invoice_id = inv.get("id", "")
    amount_cents = inv.get("amount_paid", 0)
    amount_usd = round(amount_cents / 100, 2)
    now_iso = datetime.now(timezone.utc).isoformat()

    sb_url = settings.supabase_url
    sb_key = settings.supabase_service_role_key
    if not sb_url or not sb_key:
        log.warning("stripe_webhook_no_supabase_config")
        return

    headers = {
        "apikey": sb_key,
        "Authorization": f"Bearer {sb_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    base = sb_url.rstrip("/")

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. revenue_event (idempotent via UNIQUE stripe_event_id)
        try:
            r = await client.post(
                f"{base}/rest/v1/revenue_events",
                json={
                    "event_type": "invoice_paid",
                    "amount_usd": amount_usd,
                    "stripe_event_id": event_id,
                    "occurred_at": now_iso,
                    "meta": {"stripe_invoice_id": stripe_invoice_id},
                },
                headers=headers,
            )
            if r.status_code >= 400 and r.status_code != 409:
                log.warning(
                    "stripe_revenue_event_insert_status",
                    status=r.status_code,
                    body=r.text[:200],
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("stripe_revenue_event_insert_failed", error=str(exc))

        # 2. invoice → paid
        if stripe_invoice_id:
            try:
                r = await client.patch(
                    f"{base}/rest/v1/invoices",
                    params={"stripe_invoice_id": f"eq.{stripe_invoice_id}"},
                    json={"status": "paid", "paid_at": now_iso},
                    headers=headers,
                )
                if r.status_code >= 400:
                    log.warning(
                        "stripe_invoice_update_status",
                        status=r.status_code,
                        body=r.text[:200],
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("stripe_invoice_update_failed", error=str(exc))

    log.info(
        "stripe_payment_processed",
        event_id=event_id,
        amount_usd=amount_usd,
        invoice=stripe_invoice_id,
    )


# ─────────────────────────── factory pipeline trigger ───────────────────────────

async def _trigger_factory_pipeline(event: dict[str, Any], event_id: str) -> None:
    """Handle checkout.session.completed → kick off Agentic Factory self-serve pipeline.

    Extracts `client_reference_id` (our session_id), generates a blueprint_id,
    links them in Supabase, then emits `factory.payment.confirmed` which triggers
    the FACTORY_INTAKE agent → STRATEGIST → POLISHER → DIAGRAM_DELIVERY chain.
    """
    session_obj = event.get("data", {}).get("object", {})
    session_id = session_obj.get("client_reference_id", "")
    stripe_session_id = session_obj.get("id", "")
    amount_total = session_obj.get("amount_total", 0)
    customer_email = session_obj.get("customer_details", {}).get("email", "")

    if not session_id:
        log.warning(
            "stripe_checkout_no_client_reference_id",
            stripe_session_id=stripe_session_id,
            event_id=event_id,
        )
        return

    blueprint_id = str(uuid.uuid4())
    amount_usd = round(amount_total / 100, 2)
    now_iso = datetime.now(timezone.utc).isoformat()

    sb_url = settings.supabase_url
    sb_key = settings.supabase_service_role_key
    if not sb_url or not sb_key:
        log.warning("stripe_checkout_no_supabase_config", session_id=session_id)
        return

    headers = {
        "apikey": sb_key,
        "Authorization": f"Bearer {sb_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    base = sb_url.rstrip("/")

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. Link blueprint_id to the factory_session and mark payment confirmed
        try:
            r = await client.patch(
                f"{base}/rest/v1/factory_sessions",
                params={"session_id": f"eq.{session_id}"},
                json={
                    "blueprint_id": blueprint_id,
                    "status": "payment_confirmed",
                    "stripe_session_id": stripe_session_id,
                    "amount_paid_usd": amount_usd,
                    "paid_at": now_iso,
                },
                headers=headers,
            )
            if r.status_code >= 400:
                log.warning(
                    "stripe_checkout_session_update_failed",
                    session_id=session_id,
                    status=r.status_code,
                    body=r.text[:200],
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("stripe_checkout_session_update_error", error=str(exc))

        # 2. Record the revenue event (idempotent via UNIQUE stripe_event_id)
        try:
            await client.post(
                f"{base}/rest/v1/revenue_events",
                json={
                    "event_type": "checkout_paid",
                    "amount_usd": amount_usd,
                    "stripe_event_id": event_id,
                    "occurred_at": now_iso,
                    "meta": {
                        "stripe_session_id": stripe_session_id,
                        "factory_session_id": session_id,
                        "blueprint_id": blueprint_id,
                        "customer_email": customer_email,
                    },
                },
                headers=headers,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("stripe_checkout_revenue_event_failed", error=str(exc))

    # 3. Emit internal event to trigger FACTORY_INTAKE agent
    try:
        from omerion_core.events.bus import emit_event
        emit_event(
            "factory.payment.confirmed",
            "stripe",
            {
                "session_id": session_id,
                "blueprint_id": blueprint_id,
                "amount_usd": amount_usd,
                "stripe_event_id": event_id,
            },
        )
        log.info(
            "stripe_checkout_factory_pipeline_triggered",
            session_id=session_id,
            blueprint_id=blueprint_id,
            amount_usd=amount_usd,
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "stripe_checkout_pipeline_emit_failed",
            session_id=session_id,
            blueprint_id=blueprint_id,
            error=str(exc),
        )


# ─────────────────────────── route ───────────────────────────

@router.post("/stripe", response_model=StripeAck, status_code=status.HTTP_202_ACCEPTED)
async def stripe_webhook(
    request: Request,
    stripe_signature: Annotated[str | None, Header(alias="stripe-signature")] = None,
) -> StripeAck:
    """Receive Stripe billing events and persist them to Supabase.

    Configure your Stripe dashboard to POST to /webhooks/stripe on this app.
    The full path on the omerion runtime is: `/webhooks/stripe`.
    """
    if not stripe_signature:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Stripe-Signature header required"
        )
    if not settings.stripe_webhook_secret:
        log.warning("stripe_webhook_received_but_no_secret_configured")
        return StripeAck(received=True, processed=False)

    raw = await request.body()
    try:
        event = _verify_stripe_signature(raw, stripe_signature, settings.stripe_webhook_secret)
    except ValueError as exc:
        log.warning("stripe_signature_invalid", error=str(exc))
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Invalid Stripe signature: {exc}"
        ) from exc

    event_type = event.get("type", "")
    event_id = event.get("id", "")
    log.info("stripe_event_received", event_type=event_type, event_id=event_id)

    if event_type == "invoice.payment_succeeded":
        await _persist_invoice_payment(event, event_id)
        return StripeAck(received=True, processed=True, event_type=event_type, event_id=event_id)

    if event_type == "checkout.session.completed":
        await _trigger_factory_pipeline(event, event_id)
        return StripeAck(received=True, processed=True, event_type=event_type, event_id=event_id)

    # All other event types: ack-only, observability log
    return StripeAck(received=True, processed=False, event_type=event_type, event_id=event_id)
