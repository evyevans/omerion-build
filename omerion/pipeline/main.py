"""KnowledgeBaseIngestionPipeline — orchestration entry point.

Also exports `knowledge_base_router` (FastAPI APIRouter) which is mounted
in omerion_core/inbound/app.py at prefix=/webhooks/drive.

Drive push notification flow:
  1. Google Drive POSTs to POST /webhooks/drive on file change
  2. Webhook validates bearer token + Google-Resource-State header
  3. Changed files are fetched from Drive API
  4. Each file is enqueued as a FastAPI BackgroundTask
  5. KnowledgeBaseIngestionPipeline.run() processes each file end-to-end
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request

from omerion_core.inbound.signatures import require_bearer
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from pipeline.chunker import DocumentChunker
from pipeline.embedder import EmbeddingGenerator
from pipeline.extractor import FileExtractor, _drive_service
from pipeline.index import DocumentIndex
from pipeline.upserter import VectorStoreUpserter

log = get_logger("pipeline.main")

knowledge_base_router = APIRouter(prefix="/webhooks/drive", tags=["knowledge-base"])


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@knowledge_base_router.post("", dependencies=[Depends(require_bearer)])
async def drive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    google_resource_state: Optional[str] = Header(default=None, alias="X-Goog-Resource-State"),
    google_resource_id: Optional[str] = Header(default=None, alias="X-Goog-Resource-Id"),
    google_channel_id: Optional[str] = Header(default=None, alias="X-Goog-Channel-Id"),
) -> dict:
    """Receive Google Drive push notifications for Knowledge Base folder changes."""

    # Drive sends a 'sync' ping when first registering the channel — ignore it
    if google_resource_state == "sync":
        log.info("drive_webhook_sync_ping", channel_id=google_channel_id)
        return {"status": "ok", "action": "sync_ignored"}

    if google_resource_state not in ("change", "update", "add", "remove", "trash", "untrash"):
        log.info("drive_webhook_unknown_state", state=google_resource_state)
        return {"status": "ok", "action": "ignored"}

    log.info(
        "drive_webhook_received",
        resource_state=google_resource_state,
        resource_id=google_resource_id,
        channel_id=google_channel_id,
    )

    # Fetch the changed files from the Drive API
    try:
        changed_files = _fetch_changed_files(google_resource_id, google_resource_state, google_channel_id)
    except Exception as exc:  # noqa: BLE001
        log.error("drive_webhook_fetch_failed", error=str(exc))
        return {"status": "error", "detail": "could not fetch changed files"}

    event_type = "deleted" if google_resource_state in ("remove", "trash") else "updated"

    folder_label = _folder_label(google_channel_id)
    for file_meta in changed_files:
        background_tasks.add_task(
            _run_pipeline,
            file_id=file_meta["id"],
            file_name=file_meta.get("name", ""),
            mime_type=file_meta.get("mimeType", ""),
            modified_time=file_meta.get("modifiedTime", ""),
            web_view_link=file_meta.get("webViewLink", ""),
            event_type=event_type,
            folder_name=folder_label,
        )

    return {"status": "queued", "files": len(changed_files)}


_FOLDER_LABELS: dict[str, str] = {
    settings.google_kb_new_folder_id: "Knowledge Base - New",
    settings.google_agents_folder_id: "Omerion AI Agents",
}


def _folder_label(channel_id: Optional[str]) -> str:
    """Return a human-readable folder label for metadata tagging."""
    folder_id = _resolve_folder_id(channel_id)
    return _FOLDER_LABELS.get(folder_id, "Knowledge Base")


def _resolve_folder_id(channel_id: Optional[str]) -> str:
    """Look up the watched folder for this channel from drive_watch_channels.

    Falls back to GOOGLE_KB_NEW_FOLDER_ID so legacy single-folder setups
    still work without the DB row.
    """
    if channel_id:
        try:
            from omerion_core.clients.supabase_client import supabase as _sb
            row = (
                _sb.table("drive_watch_channels")
                .select("folder_id")
                .eq("channel_id", channel_id)
                .maybe_single()
                .execute()
            )
            if row.data and row.data.get("folder_id"):
                return row.data["folder_id"]
        except Exception as exc:  # noqa: BLE001
            log.warning("drive_channel_folder_lookup_failed", channel_id=channel_id, error=str(exc))

    folder_id = settings.google_kb_new_folder_id
    if not folder_id:
        raise RuntimeError("Could not resolve folder_id for Drive webhook channel")
    return folder_id


def _fetch_changed_files(resource_id: Optional[str], state: str, channel_id: Optional[str] = None) -> list[dict]:
    """Fetch metadata for files that changed in the watched folder."""
    folder_id = _resolve_folder_id(channel_id)
    service = _drive_service()

    if resource_id:
        # Try to get the specific file first
        try:
            meta = service.files().get(
                fileId=resource_id,
                fields="id,name,mimeType,modifiedTime,webViewLink,trashed",
            ).execute()
            return [meta]
        except Exception:  # noqa: BLE001
            pass

    # Fall back: list all files in the folder
    result = (
        service.files()
        .list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id,name,mimeType,modifiedTime,webViewLink)",
            pageSize=100,
        )
        .execute()
    )
    return result.get("files", [])


def _run_pipeline(
    file_id: str,
    file_name: str,
    mime_type: str,
    modified_time: str,
    web_view_link: str,
    event_type: str,
    folder_name: str = "Knowledge Base",
) -> None:
    """Blocking pipeline run — called in a FastAPI BackgroundTask thread."""
    pipeline = KnowledgeBaseIngestionPipeline()
    pipeline.run(
        file_id=file_id,
        file_name=file_name,
        mime_type=mime_type,
        modified_time=modified_time,
        web_view_link=web_view_link,
        event_type=event_type,
        folder_name=folder_name,
    )


# ── Orchestration pipeline ────────────────────────────────────────────────────

class KnowledgeBaseIngestionPipeline:
    """Tie all pipeline components together for a single file event."""

    def __init__(self) -> None:
        self._extractor = FileExtractor()
        self._chunker = DocumentChunker()
        self._embedder = EmbeddingGenerator()
        self._upserter = VectorStoreUpserter()
        self._index = DocumentIndex()

    def run(
        self,
        file_id: str,
        file_name: str,
        mime_type: str,
        modified_time: str,
        web_view_link: str,
        event_type: str,
        folder_name: str = "Knowledge Base",
    ) -> None:
        """Full pipeline for a single Drive file event.

        event_type: 'created' | 'updated' | 'deleted'
        """
        t0 = time.monotonic()
        log.info("pipeline_start", file_id=file_id, file_name=file_name, event_type=event_type)

        # ── Deletion path ──────────────────────────────────────────────────
        if event_type == "deleted":
            self._index.delete(file_id)
            log.info("pipeline_deleted", file_id=file_id, elapsed=round(time.monotonic() - t0, 2))
            return

        # ── Extract text ───────────────────────────────────────────────────
        self._index.mark_processing(file_id, file_name, mime_type)

        text = self._extractor.extract(file_id, mime_type)
        if not text:
            log.warning("pipeline_skip_no_text", file_id=file_id, mime_type=mime_type)
            self._index.save(
                file_id=file_id,
                file_name=file_name,
                content_hash="",
                chunk_count=0,
                mime_type=mime_type,
                status="failed",
                error_message="unsupported mime type or empty extraction",
            )
            return

        # ── Deduplication check ───────────────────────────────────────────
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        decision = self._index.check(file_id, content_hash)

        if decision == "skip":
            log.info("pipeline_skip_unchanged", file_id=file_id)
            return

        # If updating, purge old vectors before re-embedding
        if decision == "update":
            log.info("pipeline_update_purge_old", file_id=file_id)
            self._upserter.delete_file(file_id)

        # ── Build chunk metadata ───────────────────────────────────────────
        base_metadata: dict[str, Any] = {
            "file_id": file_id,
            "file_name": file_name,
            "folder_name": folder_name,
            "mime_type": mime_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": modified_time or datetime.now(timezone.utc).isoformat(),
            "source_url": web_view_link,
            # Pinecone standard keys
            "persona": "knowledge_base",
            "market": "general",
            "agent_type": "kb_ingestion",
            "content_date": (modified_time or datetime.now(timezone.utc).isoformat())[:10],
        }

        # ── Chunk → Embed → Upsert ────────────────────────────────────────
        try:
            chunks = self._chunker.chunk(text, base_metadata)
            if not chunks:
                raise ValueError("chunker produced zero chunks")

            chunks = self._embedder.embed(chunks)
            self._upserter.upsert(chunks)

            self._index.save(
                file_id=file_id,
                file_name=file_name,
                content_hash=content_hash,
                chunk_count=len(chunks),
                mime_type=mime_type,
                status="completed",
            )

            elapsed = round(time.monotonic() - t0, 2)
            log.info(
                "pipeline_complete",
                file_id=file_id,
                file_name=file_name,
                chunks=len(chunks),
                elapsed_s=elapsed,
            )

        except Exception as exc:  # noqa: BLE001
            log.error("pipeline_failed", file_id=file_id, error=str(exc))
            self._index.save(
                file_id=file_id,
                file_name=file_name,
                content_hash=content_hash,
                chunk_count=0,
                mime_type=mime_type,
                status="failed",
                error_message=str(exc),
            )
