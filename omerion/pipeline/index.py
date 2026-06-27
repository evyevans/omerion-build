"""DocumentIndex — deduplication and audit log backed by Supabase document_index table.

Before every ingestion run, check whether the file has changed (content_hash comparison).
After ingestion, record the result. On deletion, cascade-remove all associated vectors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from pipeline.upserter import VectorStoreUpserter

log = get_logger("pipeline.index")


class DocumentIndex:
    """CRUD wrapper around the document_index Supabase table."""

    def check(self, file_id: str, content_hash: str) -> Literal["skip", "update", "new"]:
        """Determine whether the file needs (re-)processing.

        Returns:
            'skip'   — file_id exists and hash matches (unchanged)
            'update' — file_id exists but hash differs (content changed)
            'new'    — file_id not in index
        """
        try:
            result = (
                supabase.table("document_index")
                .select("content_hash")
                .eq("file_id", file_id)
                .maybe_single()
                .execute()
            )
            row = result.data
            if row is None:
                return "new"
            if row["content_hash"] == content_hash:
                log.info("document_index_skip", file_id=file_id, reason="hash_match")
                return "skip"
            return "update"
        except Exception as exc:  # noqa: BLE001
            log.error("document_index_check_failed", file_id=file_id, error=str(exc))
            # Treat as 'new' on error so we don't silently drop updates
            return "new"

    def save(
        self,
        file_id: str,
        file_name: str,
        content_hash: str,
        chunk_count: int,
        mime_type: Optional[str],
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Insert or update the document_index record for this file."""
        row = {
            "file_id": file_id,
            "file_name": file_name,
            "content_hash": content_hash,
            "chunk_count": chunk_count,
            "mime_type": mime_type,
            "last_ingested": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "error_message": error_message,
        }
        try:
            supabase.table("document_index").upsert(row, on_conflict="file_id").execute()
            log.info("document_index_saved", file_id=file_id, status=status, chunks=chunk_count)
        except Exception as exc:  # noqa: BLE001
            log.error("document_index_save_failed", file_id=file_id, error=str(exc))

    def mark_processing(self, file_id: str, file_name: str, mime_type: Optional[str]) -> None:
        """Set status=processing before the pipeline starts (for crash recovery audit)."""
        self.save(
            file_id=file_id,
            file_name=file_name,
            content_hash="",
            chunk_count=0,
            mime_type=mime_type,
            status="processing",
        )

    def delete(self, file_id: str) -> None:
        """Remove all vectors and the index record for a deleted Drive file."""
        log.info("document_index_deleting", file_id=file_id)
        try:
            VectorStoreUpserter().delete_file(file_id)
        except Exception as exc:  # noqa: BLE001
            log.error("document_index_vector_delete_failed", file_id=file_id, error=str(exc))

        try:
            supabase.table("document_index").delete().eq("file_id", file_id).execute()
            log.info("document_index_deleted", file_id=file_id)
        except Exception as exc:  # noqa: BLE001
            log.error("document_index_record_delete_failed", file_id=file_id, error=str(exc))
