"""VectorStoreUpserter — writes embedded chunks to Pinecone and/or Supabase pgvector.

Toggle via VECTOR_STORE env var: 'pinecone' | 'supabase' | 'both' (default).
"""
from __future__ import annotations

from typing import Any

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("pipeline.upserter")

_KB_NAMESPACE = "knowledge-base"
_BATCH = 100


class PineconeUpserter:
    """Upsert chunks into the knowledge-base namespace of the omerion-legion-rag index."""

    def upsert(self, chunks: list[dict[str, Any]]) -> None:
        """Batch-upsert all chunks. Vector ID: {file_id}_{chunk_index}."""
        if not chunks:
            return
        index = pinecone_index()
        vectors = [
            {
                "id": f"{c['metadata']['file_id']}_{c['metadata']['chunk_index']}",
                "values": c["embedding"],
                "metadata": {k: v for k, v in c["metadata"].items() if isinstance(v, (str, int, float, bool))},
            }
            for c in chunks
        ]
        for start in range(0, len(vectors), _BATCH):
            batch = vectors[start : start + _BATCH]
            try:
                index.upsert(vectors=batch, namespace=_KB_NAMESPACE)
                log.info("pinecone_upsert_batch", count=len(batch))
            except Exception as exc:  # noqa: BLE001
                log.error("pinecone_upsert_failed", batch_start=start, error=str(exc))
                raise

    def delete_file(self, file_id: str) -> None:
        """Delete all vectors for a given file_id from the knowledge-base namespace."""
        try:
            index = pinecone_index()
            index.delete(filter={"file_id": file_id}, namespace=_KB_NAMESPACE)
            log.info("pinecone_delete_file", file_id=file_id)
        except Exception as exc:  # noqa: BLE001
            log.error("pinecone_delete_failed", file_id=file_id, error=str(exc))
            raise


class SupabaseUpserter:
    """Upsert chunks into the document_chunks Supabase table (pgvector)."""

    def upsert(self, chunks: list[dict[str, Any]]) -> None:
        """Batch-upsert using ON CONFLICT (file_id, chunk_index) DO UPDATE."""
        if not chunks:
            return
        rows = [
            {
                "file_id": c["metadata"]["file_id"],
                "chunk_index": c["metadata"]["chunk_index"],
                "content": c["text"],
                # pgvector expects the vector as a string "[0.1, 0.2, ...]"
                "embedding": "[" + ",".join(str(v) for v in c["embedding"]) + "]",
                "metadata": {k: v for k, v in c["metadata"].items()},
            }
            for c in chunks
        ]
        for start in range(0, len(rows), _BATCH):
            batch = rows[start : start + _BATCH]
            try:
                supabase.table("document_chunks").upsert(
                    batch, on_conflict="file_id,chunk_index"
                ).execute()
                log.info("supabase_upsert_batch", count=len(batch))
            except Exception as exc:  # noqa: BLE001
                log.error("supabase_upsert_failed", batch_start=start, error=str(exc))
                raise

    def delete_file(self, file_id: str) -> None:
        """Delete all document_chunks rows for a given file_id."""
        try:
            supabase.table("document_chunks").delete().eq("file_id", file_id).execute()
            log.info("supabase_delete_file", file_id=file_id)
        except Exception as exc:  # noqa: BLE001
            log.error("supabase_delete_failed", file_id=file_id, error=str(exc))
            raise


class VectorStoreUpserter:
    """Factory that delegates to Pinecone and/or Supabase based on VECTOR_STORE setting."""

    def __init__(self) -> None:
        mode = (settings.vector_store or "both").lower()
        self._pinecone = PineconeUpserter() if mode in ("pinecone", "both") else None
        self._supabase = SupabaseUpserter() if mode in ("supabase", "both") else None

    def upsert(self, chunks: list[dict[str, Any]]) -> None:
        """Run upsert on all configured stores; errors in one do not abort the other."""
        if self._pinecone:
            try:
                self._pinecone.upsert(chunks)
            except Exception as exc:  # noqa: BLE001
                log.error("vector_store_pinecone_upsert_error", error=str(exc))

        if self._supabase:
            try:
                self._supabase.upsert(chunks)
            except Exception as exc:  # noqa: BLE001
                log.error("vector_store_supabase_upsert_error", error=str(exc))

    def delete_file(self, file_id: str) -> None:
        """Delete all vectors for a file from all configured stores."""
        if self._pinecone:
            try:
                self._pinecone.delete_file(file_id)
            except Exception as exc:  # noqa: BLE001
                log.error("vector_store_pinecone_delete_error", file_id=file_id, error=str(exc))

        if self._supabase:
            try:
                self._supabase.delete_file(file_id)
            except Exception as exc:  # noqa: BLE001
                log.error("vector_store_supabase_delete_error", file_id=file_id, error=str(exc))
