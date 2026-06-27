"""HTTP health sidecar for the Discord bot process.

The omerion FastAPI app exposes `/health` and `/api/v1/health` directly, so
the API service does not need this sidecar. The Discord bot runs as its own
process with no HTTP server — Railway has no signal to detect a hung
container without one. This module spins a stdlib-only async HTTP server on
a dedicated port and publishes a JSON liveness document.

Stdlib-only by design — stays alive even when heavier imports fail.

Routes:
  * GET / — minimal `{"ok": true, ...}` for Railway's healthcheckPath
  * GET /health — alias of /

Migrated from the deprecated `core/runtime/health_sidecar.py`. The original
also served the worker process; that service is being removed in Wave 0
(see plan, deploy migration § "delete omerion-worker").
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Callable

log = logging.getLogger("omerion.health_sidecar")

# Process-wide last-tick timestamp. Loop bodies in the bot update this and
# the health endpoint exposes how stale it is.
_last_tick: float = time.time()
_started_at: float = time.time()
_extra_status_provider: Callable[[], dict] | None = None


def heartbeat() -> None:
    """Call from each loop iteration so the health endpoint shows liveness."""
    global _last_tick
    _last_tick = time.time()


def set_extra_status(fn: Callable[[], dict]) -> None:
    """Optional: provide extra fields (e.g. discord_connected) to the JSON
    response. Function is called per-request and should be cheap."""
    global _extra_status_provider
    _extra_status_provider = fn


def _status_payload(service: str) -> dict:
    uptime = time.time() - _started_at
    since_tick = time.time() - _last_tick
    extra: dict = {}
    if _extra_status_provider is not None:
        try:
            extra = _extra_status_provider() or {}
        except Exception:  # noqa: BLE001 — health endpoint must never crash
            log.exception("health_extra_status_failed")
    return {
        "ok": since_tick < 7200,  # >2h stale → unhealthy
        "service": service,
        "uptime_seconds": int(uptime),
        "seconds_since_last_tick": int(since_tick),
        **extra,
    }


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                  *, service: str) -> None:
    try:
        await reader.readuntil(b"\r\n\r\n")  # consume request headers
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.close()
        return
    payload = _status_payload(service)
    body = json.dumps(payload).encode()
    status_line = (
        b"HTTP/1.1 200 OK\r\n" if payload["ok"] else b"HTTP/1.1 503 Service Unavailable\r\n"
    )
    headers = (
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n"
    )
    writer.write(status_line + headers + body)
    try:
        await writer.drain()
    finally:
        writer.close()


async def serve(service: str, *, port: int) -> None:
    """Run the health server forever. Spawn as an asyncio task from the
    service's main()."""
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, service=service),
        host="0.0.0.0",
        port=port,
    )
    log.info("health_sidecar_listening service=%s port=%s", service, port)
    async with server:
        await server.serve_forever()


def port_for(service: str) -> int:
    """Read the per-service port from env, with sensible defaults."""
    env_var = f"{service.upper()}_HEALTH_PORT"
    raw = os.getenv(env_var)
    if raw:
        return int(raw)
    return {"bot": 8002}.get(service, 8003)
