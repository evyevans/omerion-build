"""Drive → newsletter_materials sync for the newsletter_generator agent.

Expected Google Drive folder structure:

    Root folder (newsletter_skillpack_drive_folder_id)
    ├── Real Estate/          ← industry subfolder (any name → becomes industry label)
    │   ├── June/             ← month subfolder (full month name, any case)
    │   │   ├── Week 1/       ← week-slot subfolder (sent on the 1st)
    │   │   └── Week 2/       ← week-slot subfolder (sent on the 15th)
    │   └── July/ ...
    └── Technology/ ...

Each "Week N" folder is synced as ONE newsletter_materials row. The skill files
inside the folder populate content_meta (up to 4 skill cards in the email
template). The folder's Drive link becomes the material's drive_url.

Dedup: drive_file_id uniqueness in newsletter_materials prevents re-inserting
the same week-slot folder across runs.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone

from omerion_core.clients.google_client import drive_service
from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.agents.newsletter_generator.drive_sync")

_MODE_CONFIG: dict[str, tuple[str, tuple[str, ...]]] = {
    "skillpack": ("skill_pack", ("newsletter_skillpack_drive_folder_id", "newsletter_drive_folder_id")),
    "playbook": ("playbook", ("newsletter_playbook_drive_folder_id", "newsletter_drive_folder_id")),
}


def _resolve_folder_id(mode: str) -> str | None:
    _type, attrs = _MODE_CONFIG[mode]
    for attr in attrs:
        value = (getattr(settings, attr, "") or "").strip()
        if value:
            return value
    return None


def _list_folder_children(folder_id: str) -> list[dict]:
    """Return all non-trashed children of a Drive folder."""
    try:
        resp = (
            drive_service()
            .files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                orderBy="name",
                pageSize=100,
                fields="files(id, name, webViewLink, mimeType)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        return resp.get("files") or []
    except Exception as exc:  # noqa: BLE001
        log.error("drive_list_failed", folder_id=folder_id, error=str(exc))
        return []


def _is_folder(item: dict) -> bool:
    return item.get("mimeType", "").endswith(".folder")


def _find_subfolder_by_keyword(children: list[dict], keyword: str) -> dict | None:
    """Return the first folder child whose name contains keyword (case-insensitive)."""
    kw = keyword.lower()
    for item in children:
        if _is_folder(item) and kw in item["name"].lower():
            return item
    return None


def _find_week_folder(children: list[dict], week_slot: str) -> dict | None:
    """Locate the week-slot folder, accepting: 'Week 1', 'week1', 'week-1', etc."""
    slot_digit = week_slot[-1]  # "1" or "2"
    for item in children:
        if not _is_folder(item):
            continue
        normalised = item["name"].lower().replace(" ", "").replace("-", "")
        if normalised in (f"week{slot_digit}",) or item["name"].lower().startswith(
            f"week {slot_digit}"
        ):
            return item
    return None


def _current_month_name() -> str:
    return calendar.month_name[datetime.now(timezone.utc).month]  # e.g. "June"


def _current_week_slot() -> str:
    """Return 'Week 1' (days 1-14) or 'Week 2' (days 15-end) based on today's UTC date."""
    return "Week 1" if datetime.now(timezone.utc).day <= 14 else "Week 2"


def sync_drive_materials(mode: str) -> int:
    """Walk root → industry → current_month → current_week_slot and upsert the pack.

    Returns the number of new rows inserted into newsletter_materials.
    Already-synced week folders (by drive_file_id) are skipped, making this
    safe to call multiple times within the same send window.
    """
    if mode not in _MODE_CONFIG:
        log.info("drive_sync_skipped_mode", mode=mode)
        return 0

    folder_id = _resolve_folder_id(mode)
    if not folder_id:
        log.warning("drive_sync_no_folder_configured", mode=mode)
        return 0

    material_type, _ = _MODE_CONFIG[mode]
    month_name = _current_month_name()
    week_slot = _current_week_slot()
    sequence = 1 if week_slot == "Week 1" else 2

    log.info("drive_sync_start", mode=mode, month=month_name, week_slot=week_slot)

    # ── Level 1: industry subfolders ──────────────────────────────────────
    industry_folders = [f for f in _list_folder_children(folder_id) if _is_folder(f)]
    if not industry_folders:
        log.warning("drive_sync_no_industry_folders", root_folder_id=folder_id)
        return 0

    inserted = 0

    for ind_folder in industry_folders:
        industry = (
            ind_folder["name"].replace("_", " ").replace("-", " ").strip().title()
        )

        # ── Level 2: month subfolder ──────────────────────────────────────
        month_children = _list_folder_children(ind_folder["id"])
        month_folder = _find_subfolder_by_keyword(month_children, month_name)
        if not month_folder:
            log.info("drive_sync_no_month_folder", industry=industry, month=month_name)
            continue

        # ── Level 3: week-slot subfolder ──────────────────────────────────
        week_children = _list_folder_children(month_folder["id"])
        week_folder = _find_week_folder(week_children, week_slot)
        if not week_folder:
            log.info(
                "drive_sync_no_week_folder",
                industry=industry,
                month=month_name,
                week=week_slot,
            )
            continue

        # ── Dedup by drive_file_id ─────────────────────────────────────────
        existing = (
            supabase.table("newsletter_materials")
            .select("id")
            .eq("drive_file_id", week_folder["id"])
            .execute()
        )
        if existing.data:
            log.info(
                "drive_sync_already_synced",
                industry=industry,
                week=week_slot,
                drive_file_id=week_folder["id"],
            )
            continue

        # ── Skill files inside the week folder → content_meta ─────────────
        skill_files = _list_folder_children(week_folder["id"])
        if not skill_files:
            log.warning(
                "drive_sync_empty_week_folder", industry=industry, week=week_slot
            )
            continue

        content_meta: dict = {
            "pack_name": f"{industry} {month_name} {week_slot} Skills Pack",
            "target_model_family": "Claude 4.5 & Higher",
            "pack_lede": (
                f"A curated set of agentic skills tailored for {industry} professionals."
            ),
            "skill_count": str(len(skill_files)),
            "model_count": "4",
            "use_case": industry,
            "cta_headline": "Ready to upgrade your system?",
            "cta_desc": "Deploy these skills instantly into your active agent architecture.",
            "learn_url": "https://omerion.io",
        }
        for i, sf in enumerate(skill_files, start=1):
            if i > 4:
                break
            skill_name = (
                re.sub(r"\.[^.]+$", "", sf["name"])
                .replace("_", " ")
                .replace("-", " ")
                .title()
            )
            content_meta[f"skill_{i}_name"] = skill_name
            content_meta[f"skill_{i}_trigger"] = "Natural Language"
            content_meta[f"skill_{i}_desc"] = (
                f"Advanced {industry.lower()} automation skill for Claude agents."
            )

        title = f"{industry} — {month_name} {week_slot} Skills Pack"

        try:
            supabase.table("newsletter_materials").insert(
                {
                    "industry": industry,
                    "material_type": material_type,
                    "title": title,
                    "drive_url": week_folder.get("webViewLink") or "",
                    "sequence_number": sequence,
                    "drive_file_id": week_folder["id"],
                    # Omit created_at → DB defaults to now(), so the 14-day
                    # recency filter in generate_and_send always passes even
                    # when the Drive folder was created weeks in advance.
                    "content_meta": content_meta,
                }
            ).execute()
            inserted += 1
            log.info(
                "drive_sync_inserted",
                industry=industry,
                week=week_slot,
                title=title,
                skills=len(skill_files),
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "drive_sync_insert_failed",
                industry=industry,
                week=week_slot,
                error=str(exc),
            )

    log.info(
        "drive_sync_complete",
        mode=mode,
        month=month_name,
        week_slot=week_slot,
        new_materials=inserted,
    )
    return inserted
