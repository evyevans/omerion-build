"""LLM layer: Claude router + OpenAI embeddings + prompt-caching helpers."""
from omerion_core.llm.cache import (
    MIN_CACHE_TOKENS,
    cached_blocks,
    make_cached_system,
    token_estimate,
)
from omerion_core.llm.embeddings import embed, embed_batch
from omerion_core.llm.router import ClaudeRouter, Tier, claude

__all__ = [
    "ClaudeRouter",
    "Tier",
    "claude",
    "embed",
    "embed_batch",
    "make_cached_system",
    "cached_blocks",
    "token_estimate",
    "MIN_CACHE_TOKENS",
]
