"""In-process per-IP rate limiter for inbound webhooks.

No new dependency — uses a thread-safe in-memory token bucket. Sized for
single-process FastAPI deployments (Railway runs one uvicorn worker per
service). If we ever scale horizontally, swap the backing store for Redis
or Supabase — the public interface (`limit(name, per_minute)`) stays the
same.

Use as a FastAPI dependency on routes that accept bearer-authenticated
external traffic. The dependency is keyed on (route_name, client_ip), so
hitting /hitl/resolve does not deplete /webhooks/fireflies budget.

Whitelist: callers from 127.0.0.1 or with X-Forwarded-For == loopback
are exempt (local Discord bot, dev curl). Production callers always go
through the proxy and present a real X-Forwarded-For.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Callable

from fastapi import HTTPException, Request, status

from omerion_core.logging import get_logger

log = get_logger("omerion.inbound.rate_limit")

_LOCK = threading.Lock()
# bucket key → (tokens_remaining, last_refill_unix)
_BUCKETS: dict[tuple[str, str], tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))

_LOOPBACK_IPS = {"127.0.0.1", "::1", "testclient"}


def _client_ip(request: Request) -> str:
    """Trust the leftmost X-Forwarded-For if present; else fall back to client.host."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _refill_and_take(key: tuple[str, str], capacity: float, refill_per_sec: float) -> bool:
    """Token-bucket: refill since last touch, then try to deduct 1.

    Atomic under the module lock — single FastAPI worker means all callers
    serialize through the same Python interpreter; the lock just protects
    against the two GIL switches that can happen mid-arithmetic.
    """
    now = time.time()
    with _LOCK:
        tokens, last = _BUCKETS[key]
        if last == 0.0:
            tokens = capacity
        else:
            elapsed = max(0.0, now - last)
            tokens = min(capacity, tokens + elapsed * refill_per_sec)
        if tokens < 1.0:
            _BUCKETS[key] = (tokens, now)
            return False
        _BUCKETS[key] = (tokens - 1.0, now)
        return True


def limit(name: str, per_minute: int) -> Callable[[Request], None]:
    """Return a FastAPI dependency that enforces `per_minute` requests per IP.

    Burst capacity equals `per_minute` (token bucket starts full). Steady-state
    refill is `per_minute / 60` tokens per second.
    """
    capacity = float(per_minute)
    refill_per_sec = capacity / 60.0

    def _dep(request: Request) -> None:
        ip = _client_ip(request)
        if ip in _LOOPBACK_IPS:
            return
        if not _refill_and_take((name, ip), capacity, refill_per_sec):
            log.warning("rate_limit_exceeded", route=name, client_ip=ip, per_minute=per_minute)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"rate limit exceeded for {name}; max {per_minute}/min per IP",
            )

    _dep.__name__ = f"rate_limit_{name}"
    return _dep
