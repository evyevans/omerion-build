"""Inbound auth dependencies.

Two styles:
  - `require_bearer` — trusted internal callers (Discord bot, Apps Script, cron).
  - `verify_fireflies_signature` — HMAC-SHA256 over raw request body.

Neither helper uses `secrets.compare_digest` directly on user-controlled
input; every comparison goes through constant-time primitives.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException, Request, status

from omerion_core.settings import settings


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    expected = settings.omerion_webhook_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook token not configured")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    presented = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(presented, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bearer token")


async def verify_fireflies_signature(request: Request) -> bytes:
    """Fireflies signs with HMAC-SHA256 over the raw request body.

    Returns the raw body so the route handler can parse it once without
    re-reading the stream.
    """
    secret = settings.fireflies_webhook_secret
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "fireflies webhook secret missing")
    signature = request.headers.get("x-fireflies-signature") or request.headers.get("x-hub-signature-256", "")
    if not signature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing signature header")
    body = await request.body()
    mac = hmac.HMAC(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    presented = signature.split("=", 1)[-1].strip()
    if not hmac.compare_digest(mac, presented):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "signature mismatch")
    return body
