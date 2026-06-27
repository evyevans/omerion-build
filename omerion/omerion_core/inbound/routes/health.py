"""GET /health/services — per-connector connectivity ping for the dashboard.

Each check is cheap and isolated: a failure in one service never affects
the others, and unconfigured services report `disconnected` rather than
throwing. The response shape matches the dashboard's `SystemService`:
  { "services": [{ "name": str, "status": connected|degraded|disconnected, "latencyMs": int }] }

Status thresholds:
  connected     — HTTP 2xx AND latency <  750 ms
  degraded      — HTTP 2xx AND latency >= 750 ms (or soft failure)
  disconnected  — unconfigured or hard failure
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

import httpx
from fastapi import APIRouter

from omerion_core.logging import get_logger
from omerion_core.settings import settings

router = APIRouter(tags=["health"])
log = get_logger("omerion.inbound.health")

_DEGRADED_MS = 750
_TIMEOUT_S = 4.0


def _status(ok: bool, latency_ms: int) -> str:
    if not ok:
        return "disconnected"
    return "degraded" if latency_ms >= _DEGRADED_MS else "connected"


async def _timed(name: str, coro_factory: Callable[[], Awaitable[bool]]) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        ok = await asyncio.wait_for(coro_factory(), timeout=_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — health checks swallow all failures
        log.warning("health_check_failed", service=name, error=str(exc))
        ok = False
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return {"name": name, "status": _status(ok, latency_ms), "latencyMs": latency_ms}


async def _check_supabase() -> bool:
    if not settings.supabase_url:
        return False
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        # REST root returns 200 with valid anon/service keys.
        r = await client.get(
            f"{settings.supabase_url}/rest/v1/",
            headers={"apikey": settings.supabase_service_role_key or settings.supabase_anon_key or ""},
        )
        return r.status_code < 500


async def _check_anthropic() -> bool:
    if not settings.anthropic_api_key:
        return False
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        # /v1/models requires auth and is cheap.
        r = await client.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        return r.status_code < 500


async def _check_openai() -> bool:
    if not settings.openai_api_key:
        return False
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        r = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )
        return r.status_code < 500


async def _check_pinecone() -> bool:
    if not settings.pinecone_api_key:
        return False
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        r = await client.get(
            "https://api.pinecone.io/indexes",
            headers={"Api-Key": settings.pinecone_api_key},
        )
        return r.status_code < 500


async def _check_google() -> bool:
    if not (
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret
        and settings.google_oauth_refresh_token
    ):
        return False
    # Refresh a short-lived access token — confirms OAuth creds are valid.
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        r = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "refresh_token": settings.google_oauth_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        return r.status_code == 200


async def _check_github() -> bool:
    if not settings.github_token:
        return False
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        r = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        return r.status_code == 200


async def _check_fireflies() -> bool:
    if not settings.fireflies_api_key:
        return False
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        r = await client.post(
            "https://api.fireflies.ai/graphql",
            headers={
                "Authorization": f"Bearer {settings.fireflies_api_key}",
                "Content-Type": "application/json",
            },
            json={"query": "{ user { user_id } }"},
        )
        return r.status_code < 500


_CHECKS: list[tuple[str, Callable[[], Awaitable[bool]]]] = [
    ("Supabase", _check_supabase),
    ("Claude API", _check_anthropic),
    ("OpenAI", _check_openai),
    ("Pinecone", _check_pinecone),
    ("Google Workspace", _check_google),
    ("GitHub", _check_github),
    ("Fireflies", _check_fireflies),
]


@router.get("/health/services")
async def health_services() -> dict[str, Any]:
    results = await asyncio.gather(*(_timed(name, fn) for name, fn in _CHECKS))
    return {"services": results}
