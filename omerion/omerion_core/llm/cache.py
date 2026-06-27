"""Prompt-caching helpers.

Anthropic prompt caching charges a 25% write premium but reads at 10% of
base. Only worth it when the *same* system prompt is reused across many
requests inside 5 minutes AND the prompt is ≥ MIN_CACHE_TOKENS. Typical
wins: the giant ICP rubric (icp_scoring), the W5H extraction prompt
(meeting_intelligence), and the offer-package catalog (offer_matching).

Usage:
    from omerion_core.llm import claude, Tier, make_cached_system

    result = claude().complete(
        tier=Tier.HEAVY,
        system=make_cached_system(BIG_PROMPT),   # dict with cache_control
        messages=[{"role": "user", "content": user_input}],
    )

The router accepts either a raw `str` or a list of system blocks. When
you pass a list, each block can carry `cache_control`.
"""
from __future__ import annotations

from typing import Any

# Anthropic minimum for a cacheable block is 1024 tokens for Sonnet/Opus,
# 2048 for Haiku. We pick 1024 as the lower bound; Haiku callers either
# accept the no-op or use the token_estimate() helper below.
MIN_CACHE_TOKENS = 1024

# Rough 4-chars-per-token heuristic (English; conservative for code/JSON).
_CHARS_PER_TOKEN = 4.0


def token_estimate(text: str) -> int:
    """Quick char-based token estimate — cheap, approximate, good enough."""
    return int(len(text) / _CHARS_PER_TOKEN)


def make_cached_system(text: str, *, ttl: str = "5m") -> list[dict[str, Any]] | str:
    """Wrap a long system prompt as a cacheable block.

    Returns a `str` if the prompt is too short to be worth caching (falls
    back to the uncached path transparently), or a list of blocks with
    `cache_control` attached.
    """
    if token_estimate(text) < MIN_CACHE_TOKENS:
        return text
    return [
        {
            "type": "text",
            "text": text,
            "cache_control": {"type": "ephemeral", "ttl": ttl},
        }
    ]


def cached_blocks(*blocks: str, ttl: str = "5m") -> list[dict[str, Any]]:
    """Build a multi-block system with cache_control on the last block only.

    Use when the system prompt has a short dynamic preamble followed by a
    large static body. The cache breakpoint goes on the final block so the
    dynamic preamble doesn't invalidate the cached portion.
    """
    if not blocks:
        raise ValueError("at least one block required")
    out: list[dict[str, Any]] = [{"type": "text", "text": b} for b in blocks]
    out[-1]["cache_control"] = {"type": "ephemeral", "ttl": ttl}
    return out
