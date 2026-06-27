"""Google Drive push-notification channel management.

Registers a watch channel on the Knowledge Base folder and auto-renews it
before the 7-day TTL expires. Run as a standalone script to register:

    python -m pipeline.watcher

The scheduler job renew_drive_channels() is registered in main.py lifespan
via APScheduler and runs daily at 03:00 ET.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("pipeline.watcher")

_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_CHANNEL_TTL_SECONDS = 604_800  # 7 days — Drive maximum
_RENEW_BEFORE_SECONDS = 86_400   # renew if expiring within 24 h


@lru_cache(maxsize=1)
def _drive_service():
    sa_json = settings.google_service_account_json
    if not sa_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON must be set")
    with open(sa_json) as f:
        info = json.load(f)
    creds = service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def register_drive_channel(folder_id: Optional[str] = None) -> dict:
    """Register a push-notification channel on the Knowledge Base folder.

    Stores the channel metadata in drive_watch_channels for renewal tracking.
    Returns the channel dict from the Drive API.
    """
    folder_id = folder_id or settings.google_kb_new_folder_id
    if not folder_id:
        raise RuntimeError("GOOGLE_KB_NEW_FOLDER_ID must be set")

    webhook_url = settings.omerion_public_base_url.rstrip("/") + "/webhooks/drive"
    channel_id = str(uuid.uuid4())
    expiry_ms = int(
        (datetime.now(timezone.utc) + timedelta(seconds=_CHANNEL_TTL_SECONDS)).timestamp() * 1000
    )

    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
        "expiration": expiry_ms,
    }

    service = _drive_service()
    # Watch changes to files within the folder
    response = (
        service.files()
        .watch(fileId=folder_id, body=body)
        .execute()
    )

    resource_id = response.get("resourceId", "")
    expires_at = datetime.fromtimestamp(int(response.get("expiration", expiry_ms)) / 1000, tz=timezone.utc)

    # Persist for renewal
    supabase.table("drive_watch_channels").upsert(
        {
            "channel_id": channel_id,
            "resource_id": resource_id,
            "expires_at": expires_at.isoformat(),
            "folder_id": folder_id,
        },
        on_conflict="channel_id",
    ).execute()

    log.info(
        "drive_channel_registered",
        channel_id=channel_id,
        resource_id=resource_id,
        expires_at=expires_at.isoformat(),
        webhook_url=webhook_url,
    )
    return response


def stop_drive_channel(channel_id: str, resource_id: str) -> None:
    """Stop an active Drive push-notification channel."""
    try:
        service = _drive_service()
        service.channels().stop(body={"id": channel_id, "resourceId": resource_id}).execute()
        log.info("drive_channel_stopped", channel_id=channel_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("drive_channel_stop_failed", channel_id=channel_id, error=str(exc))


def renew_drive_channels() -> None:
    """Called daily by APScheduler — re-register channels expiring within 24 h."""
    cutoff = (datetime.now(timezone.utc) + timedelta(seconds=_RENEW_BEFORE_SECONDS)).isoformat()
    try:
        result = (
            supabase.table("drive_watch_channels")
            .select("channel_id, resource_id, folder_id")
            .lt("expires_at", cutoff)
            .execute()
        )
        channels = result.data or []
    except Exception as exc:  # noqa: BLE001
        log.error("drive_channel_renewal_query_failed", error=str(exc))
        return

    for ch in channels:
        try:
            stop_drive_channel(ch["channel_id"], ch["resource_id"])
            register_drive_channel(folder_id=ch["folder_id"])
            supabase.table("drive_watch_channels").delete().eq("channel_id", ch["channel_id"]).execute()
            log.info("drive_channel_renewed", old_channel_id=ch["channel_id"])
        except Exception as exc:  # noqa: BLE001
            log.error("drive_channel_renewal_failed", channel_id=ch["channel_id"], error=str(exc))


def register_all_drive_channels() -> None:
    """Register watch channels for all configured Drive folders.

    Idempotent: Drive will issue a new channel each time, old ones expire naturally.
    """
    folders = {
        "Knowledge Base - New": settings.google_kb_new_folder_id,
        "Omerion AI Agents": settings.google_agents_folder_id,
    }
    for label, folder_id in folders.items():
        if not folder_id:
            log.warning("drive_channel_skip_missing_folder_id", label=label)
            continue
        try:
            register_drive_channel(folder_id=folder_id)
            print(f"[OK] Drive watch channel registered for: {label} ({folder_id})")
        except Exception as exc:  # noqa: BLE001
            print(f"[ERROR] Failed to register channel for {label}: {exc}")


if __name__ == "__main__":
    register_all_drive_channels()
