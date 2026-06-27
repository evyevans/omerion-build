"""Rate-limited HTTP with transient/permanent error classification."""

from __future__ import annotations

import time
from typing import Any, Iterable

import httpx

from omerion_core.logging import get_logger
from omerion_core.rate_limit.token_bucket import BUCKETS, TokenBucket

log = get_logger("omerion.http")

_DEFAULT_BUCKET = TokenBucket(rate_per_sec=1.0, burst=3)

# Extend shared buckets with services used by safe_request callers.
_SERVICE_BUCKETS: dict[str, TokenBucket] = {
    **BUCKETS,
    "lever": TokenBucket(rate_per_sec=1.0, burst=2),
    "greenhouse": TokenBucket(rate_per_sec=1.0, burst=2),
}


class TransientHTTPError(Exception):
    """Retryable HTTP/network failure (429, 5xx, timeouts)."""


class PermanentHTTPError(Exception):
    """Non-retryable HTTP failure (unexpected 4xx)."""

    def __init__(self, message: str, *, status: int, body_excerpt: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body_excerpt = body_excerpt


def _bucket_for(service: str | None) -> TokenBucket:
    if not service:
        return _DEFAULT_BUCKET
    return _SERVICE_BUCKETS.get(service) or _DEFAULT_BUCKET


def _is_transient_status(code: int) -> bool:
    return code == 429 or code >= 500


def safe_request(
    method: str,
    url: str,
    *,
    service: str | None = None,
    headers: dict[str, str] | None = None,
    json: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
    attempts: int = 3,
    expected_status: Iterable[int] = (200,),
) -> httpx.Response:
    """Issue an HTTP request with per-service rate limiting and bounded retries."""
    allowed = set(expected_status)
    last_exc: Exception | None = None

    for attempt in range(1, max(1, attempts) + 1):
        _bucket_for(service).acquire()
        try:
            resp = httpx.request(
                method,
                url,
                headers=headers,
                json=json,
                params=params,
                timeout=timeout,
                follow_redirects=True,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = TransientHTTPError(str(exc))
            if attempt >= attempts:
                raise last_exc from exc
            time.sleep(min(2 ** attempt, 10))
            continue
        except httpx.HTTPError as exc:
            last_exc = TransientHTTPError(str(exc))
            if attempt >= attempts:
                raise last_exc from exc
            time.sleep(min(2 ** attempt, 10))
            continue

        if resp.status_code in allowed:
            return resp

        body_excerpt = (resp.text or "")[:500]
        if _is_transient_status(resp.status_code):
            last_exc = TransientHTTPError(
                f"{method} {url} -> {resp.status_code}: {body_excerpt[:120]}"
            )
            if attempt >= attempts:
                raise last_exc
            retry_after = resp.headers.get("Retry-After")
            wait = (
                float(retry_after)
                if retry_after and str(retry_after).isdigit()
                else min(2 ** attempt, 10)
            )
            time.sleep(wait)
            continue

        raise PermanentHTTPError(
            f"{method} {url} -> {resp.status_code}",
            status=resp.status_code,
            body_excerpt=body_excerpt,
        )

    assert last_exc is not None
    raise last_exc
