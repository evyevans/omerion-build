"""EmbeddingGenerator — batched OpenAI embeddings via the shared embed_batch helper.

Reuses omerion_core.llm.embeddings.embed_batch — does NOT create its own OpenAI client.
"""
from __future__ import annotations

from typing import Any

from omerion_core.llm.embeddings import embed_batch
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("pipeline.embedder")


class EmbeddingGenerator:
    """Add 'embedding' vector to each chunk dict using batched OpenAI calls."""

    def embed(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Populate 'embedding' field on every chunk. Returns the same list, mutated."""
        if not chunks:
            return chunks

        batch_size: int = settings.embedding_batch_size
        total_chars = 0
        embedded = 0

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            texts = [c["text"] for c in batch]
            total_chars += sum(len(t) for t in texts)

            vectors = embed_batch(texts)

            for chunk, vec in zip(batch, vectors):
                chunk["embedding"] = vec
                embedded += 1

        # Rough token cost estimate: ~4 chars per token, text-embedding-3-small = $0.02/1M tokens
        estimated_tokens = total_chars // 4
        estimated_cost_usd = (estimated_tokens / 1_000_000) * 0.02
        log.info(
            "embedder_complete",
            chunks=embedded,
            estimated_tokens=estimated_tokens,
            estimated_cost_usd=round(estimated_cost_usd, 6),
        )
        return chunks
