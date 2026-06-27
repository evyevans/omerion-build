"""Token bucket — per-service rate limiting.

Usage:
    _bucket = TokenBucket(rate_per_sec=1.0, burst=3)

    @rate_limited(_bucket)
    def send_message(to, body): ...
"""
from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Callable, TypeVar

T = TypeVar("T")


class TokenBucket:
    def __init__(self, rate_per_sec: float, burst: int | None = None) -> None:
        self._rate = rate_per_sec
        self._capacity = burst if burst is not None else max(1, int(rate_per_sec))
        self._tokens = float(self._capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        delta = now - self._last
        self._tokens = min(self._capacity, self._tokens + delta * self._rate)
        self._last = now

    def acquire(self, n: float = 1.0) -> None:
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return
                shortfall = n - self._tokens
                wait = shortfall / self._rate
            time.sleep(wait)


def rate_limited(bucket: TokenBucket) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            bucket.acquire()
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ─── Shared per-service buckets ────────────────────────────────────
BUCKETS = {
    "gmail":        TokenBucket(rate_per_sec=4.0, burst=10),   # well under 250/user-sec
    "sheets":       TokenBucket(rate_per_sec=2.0, burst=5),
    "pinecone":     TokenBucket(rate_per_sec=20.0, burst=50),
    "openai":       TokenBucket(rate_per_sec=10.0, burst=20),
    "anthropic":    TokenBucket(rate_per_sec=5.0, burst=10),
    "github":       TokenBucket(rate_per_sec=1.0, burst=3),    # core REST limits
    "github_search":TokenBucket(rate_per_sec=0.5, burst=2),    # search API: 30 req/min authed
    "fireflies":    TokenBucket(rate_per_sec=1.0, burst=3),
    "linkedin":     TokenBucket(rate_per_sec=0.05, burst=1),   # very conservative
    "web_scraping": TokenBucket(rate_per_sec=0.5, burst=2),    # generic scraping (Apollo, Crunchbase, etc.)
    "serpapi":      TokenBucket(rate_per_sec=1.0, burst=3),    # paid tier: 100/hour ≈ 0.028/s — leave headroom
    "hunter":       TokenBucket(rate_per_sec=1.0, burst=3),    # Hunter free: 25/min, paid varies
    "firecrawl":    TokenBucket(rate_per_sec=2.0, burst=5),    # generous; Firecrawl paid tier
    "supabase_mgmt":TokenBucket(rate_per_sec=2.0, burst=5),    # Management API tighter than data API
    "railway":      TokenBucket(rate_per_sec=1.0, burst=3),    # GraphQL endpoint
    "discord":      TokenBucket(rate_per_sec=2.0, burst=5),    # webhook: 5/sec per channel
}
