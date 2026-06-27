"""bulk_sync.py — One-time (and re-runnable) ingestion of all existing Drive files.

Crawls both configured folders recursively, runs every file through the standard
KnowledgeBaseIngestionPipeline (which deduplicates via SHA-256, so re-runs are safe),
and reports a summary.

Usage:
    uv run python -m pipeline.bulk_sync
    uv run python -m pipeline.bulk_sync --folders kb       # only Knowledge Base - New
    uv run python -m pipeline.bulk_sync --folders agents   # only Omerion AI Agents
    uv run python -m pipeline.bulk_sync --dry-run          # list files without ingesting
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build

from omerion_core.logging import get_logger
from omerion_core.settings import settings
from pipeline.main import KnowledgeBaseIngestionPipeline

log = get_logger("pipeline.bulk_sync")

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

_FOLDER_MAP = {
    "kb": ("Knowledge Base - New", lambda: settings.google_kb_new_folder_id),
    "agents": ("Omerion AI Agents", lambda: settings.google_agents_folder_id),
}

# MIME types the pipeline can meaningfully process
_SUPPORTED_MIMES = {
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
}


@lru_cache(maxsize=1)
def _drive_service():
    sa_json = settings.google_service_account_json
    with open(sa_json) as f:
        info = json.load(f)
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


@dataclass
class SyncStats:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    unsupported: int = 0
    errors: list[str] = field(default_factory=list)


def _list_folder_files(folder_id: str) -> list[dict]:
    """Return all non-trashed files in a folder (non-recursive, Google Drive is flat)."""
    service = _drive_service()
    files: list[dict] = []
    page_token = None

    while True:
        kwargs: dict = {
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,webViewLink,size)",
            "pageSize": 200,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        result = service.files().list(**kwargs).execute()
        files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return files


def sync_folder(label: str, folder_id: str, dry_run: bool = False) -> SyncStats:
    """Sync all files in a single folder. Returns stats."""
    stats = SyncStats()
    pipeline = KnowledgeBaseIngestionPipeline()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Scanning: {label} ({folder_id})")
    files = _list_folder_files(folder_id)
    stats.total = len(files)
    print(f"  Found {stats.total} files")

    for i, f in enumerate(files, 1):
        mime = f.get("mimeType", "")
        name = f.get("name", "")
        fid = f["id"]

        prefix = f"  [{i}/{stats.total}] {name}"

        if mime not in _SUPPORTED_MIMES:
            print(f"{prefix} — SKIPPED (unsupported: {mime})")
            stats.unsupported += 1
            continue

        if dry_run:
            print(f"{prefix} — would ingest ({mime})")
            stats.processed += 1
            continue

        try:
            t0 = time.monotonic()
            pipeline.run(
                file_id=fid,
                file_name=name,
                mime_type=mime,
                modified_time=f.get("modifiedTime", ""),
                web_view_link=f.get("webViewLink", ""),
                event_type="updated",
                folder_name=label,
            )
            elapsed = round(time.monotonic() - t0, 1)
            print(f"{prefix} — OK ({elapsed}s)")
            stats.processed += 1
        except Exception as exc:  # noqa: BLE001
            msg = f"{name}: {exc}"
            print(f"{prefix} — ERROR: {exc}")
            log.error("bulk_sync_file_failed", file_id=fid, file_name=name, error=str(exc))
            stats.errors.append(msg)
            stats.failed += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk sync Drive folders into Pinecone/Supabase")
    parser.add_argument(
        "--folders",
        nargs="*",
        choices=["kb", "agents"],
        default=["kb", "agents"],
        help="Which folders to sync (default: both)",
    )
    parser.add_argument("--dry-run", action="store_true", help="List files without ingesting")
    args = parser.parse_args()

    all_stats: dict[str, SyncStats] = {}
    t_start = time.monotonic()

    for key in args.folders:
        label, folder_id_fn = _FOLDER_MAP[key]
        folder_id = folder_id_fn()
        if not folder_id:
            print(f"[WARN] No folder ID configured for '{label}' — skipping")
            continue
        stats = sync_folder(label, folder_id, dry_run=args.dry_run)
        all_stats[label] = stats

    elapsed = round(time.monotonic() - t_start, 1)
    print(f"\n{'='*55}")
    print(f"Bulk sync {'(dry run) ' if args.dry_run else ''}complete in {elapsed}s")
    for label, s in all_stats.items():
        print(
            f"  {label}: {s.total} total | "
            f"{s.processed} processed | "
            f"{s.unsupported} unsupported | "
            f"{s.failed} failed"
        )
    if any(s.errors for s in all_stats.values()):
        print("\nErrors:")
        for s in all_stats.values():
            for e in s.errors:
                print(f"  - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
