"""Retry presets — thin wrappers over tenacity for consistent agent behavior."""
from __future__ import annotations

from typing import Callable, TypeVar

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

T = TypeVar("T")


def transient_retry(
    *,
    attempts: int = 3,
    min_wait: float = 4,
    max_wait: float = 60,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry on transient errors (network / 5xx / 429)."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
    )


__all__ = ["transient_retry"]
