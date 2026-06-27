"""Pinecone index handle — single index with multiple namespaces.

Unified RAG Architecture:
  Single index (omerion-legion-rag, 512-dim cosine) with 9 namespaces.
  All records follow a consistent metadata schema to enable cross-agent RAG.

Standard metadata (required on every vector):
  - persona: founder | investor | operator | team_member | customer | prospect
  - industry: general_b2b | saas | fintech | healthcare | ... (any industry code)
  - agent_type: market_watcher | lead_scraper | offer_matching | etc
  - content_date: ISO 8601 timestamp
  - source_url: internal://... or https://... (where content came from)
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Any, TypedDict

from pinecone import Pinecone

from omerion_core.settings import settings

_async_index = None  # IndexAsyncio | None — set by init_async_index() in lifespan


async def init_async_index() -> None:
    """Initialize the async Pinecone index handle. Call once from FastAPI lifespan."""
    global _async_index
    if not settings.pinecone_api_key:
        return
    try:
        pc = _pc()
        host = pc.describe_index(settings.pinecone_index).host
        _async_index = pc.IndexAsyncio(host=host)
    except Exception as exc:
        from omerion_core.logging import get_logger
        get_logger("omerion.clients.pinecone").warning(
            "pinecone_async_init_failed", error=str(exc)
        )


async def close_async_index() -> None:
    """Close the async index connection pool. Call from lifespan shutdown."""
    global _async_index
    if _async_index is not None:
        try:
            await _async_index.close()
        except Exception:
            pass
        _async_index = None


def get_async_index():
    """Return the initialized async IndexAsyncio handle (or None if not ready)."""
    return _async_index


@lru_cache(maxsize=1)
def _pc() -> Pinecone:
    if not settings.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY must be set")
    return Pinecone(api_key=settings.pinecone_api_key)


@lru_cache(maxsize=1)
def pinecone_index():
    return _pc().Index(settings.pinecone_index)


class PineconeRecord(TypedDict, total=False):
    """Pinecone record with unified metadata schema.

    Required: id, values
    Standard metadata (required): persona, industry, agent_type, content_date, source_url
    Namespace-specific metadata: optional, as needed for each agent/namespace
    """
    id: str
    values: list[float]
    metadata: dict[str, Any]


def build_record(
    id: str,
    vector: list[float],
    persona: str,
    industry: str,
    agent_type: str,
    source_url: str,
    **namespace_metadata: Any,
) -> PineconeRecord:
    """Build a Pinecone record with unified schema.

    Args:
        id: Unique record ID (e.g., "account:123:summary")
        vector: 512-dim embedding vector
        persona: founder | investor | operator | team_member | customer | prospect
        industry: general_b2b | saas | fintech | healthcare | ...
        agent_type: Name of the agent creating this record
        source_url: Where the content came from (internal://... or https://...)
        **namespace_metadata: Additional fields specific to the namespace

    Returns:
        PineconeRecord ready to upsert
    """
    return PineconeRecord(
        id=id,
        values=vector,
        metadata={
            # Standard schema (required on every vector)
            "persona": persona,
            "industry": industry,
            "agent_type": agent_type,
            "content_date": datetime.utcnow().isoformat() + "Z",
            "source_url": source_url,
            # Namespace-specific metadata
            **namespace_metadata,
        },
    )
