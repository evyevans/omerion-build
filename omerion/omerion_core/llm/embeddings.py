"""OpenAI embeddings — the ONLY OpenAI call in the system.

Used by all agents that write to / query Pinecone (ICP Scoring, Meeting
Intelligence, Offer Matching, Research Dossiers, R1-R4).
"""
from __future__ import annotations

from functools import lru_cache

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from omerion_core.settings import settings


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY must be set (embeddings only)")
    return OpenAI(api_key=settings.openai_api_key)


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
def embed(text: str) -> list[float]:
    resp = _client().embeddings.create(
        model=settings.openai_embedding_model,
        input=text,
        dimensions=settings.openai_embedding_dimensions,
    )
    return resp.data[0].embedding


@retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    resp = _client().embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
        dimensions=settings.openai_embedding_dimensions,
    )
    # OpenAI returns in request order
    return [d.embedding for d in resp.data]
