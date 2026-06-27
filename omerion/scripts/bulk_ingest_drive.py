"""Bulk ingestion script for the Google Drive Knowledge Base folder.

Usage:
    cd omerion && uv run python -m scripts.bulk_ingest_drive
"""
from __future__ import annotations

import time

from omerion_core.logging import get_logger
from omerion_core.settings import settings
from pipeline.extractor import _drive_service
from pipeline.main import KnowledgeBaseIngestionPipeline

log = get_logger("scripts.bulk_ingest_drive")

def main() -> None:
    folder_id = settings.google_kb_new_folder_id
    if not folder_id:
        log.error("GOOGLE_KB_NEW_FOLDER_ID is not configured in .env")
        return

    log.info("Starting bulk ingestion of Google Drive Knowledge Base", folder_id=folder_id)
    service = _drive_service()
    
    files = []
    page_token = None
    
    # Fetch all files with pagination
    while True:
        result = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink)",
            pageSize=100,
            pageToken=page_token
        ).execute()
        
        files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
            
    log.info(f"Found {len(files)} files to ingest.")
    
    pipeline = KnowledgeBaseIngestionPipeline()
    success_count = 0
    error_count = 0
    
    for idx, f in enumerate(files, 1):
        file_id = f["id"]
        file_name = f.get("name", "Unknown")
        mime_type = f.get("mimeType", "")
        modified_time = f.get("modifiedTime", "")
        web_view_link = f.get("webViewLink", "")
        
        log.info(f"[{idx}/{len(files)}] Ingesting: {file_name}")
        
        try:
            pipeline.run(
                file_id=file_id,
                file_name=file_name,
                mime_type=mime_type,
                modified_time=modified_time,
                web_view_link=web_view_link,
                event_type="updated"  # Treated as updated to ensure chunks are pushed
            )
            success_count += 1
        except Exception as exc:
            log.error(f"Failed to ingest {file_name}: {exc}")
            error_count += 1
            
    log.info(f"Bulk ingestion complete. Success: {success_count}, Errors: {error_count}")

if __name__ == "__main__":
    main()
