"""
Intent score decay — recalculates intent_score for all contacts using a
30-day half-life on engagement signals. Designed to run daily, before the
Daily Digest. Idempotent: always recalculates from raw Outreach Log data.

BackboneEngine owns opt-out enforcement and writes; this script reads from
Sheets, calculates the new score, and hands it back via BackboneEngine endpoint.
"""
import os
import requests
from datetime import datetime, timezone, timedelta
from typing import Any

from tools.sheets_api import read_range, rows_to_dicts

_SHEET_ID = os.environ.get("COMMAND_CENTER_SHEET_ID", "")
_BACKBONE_URL = os.environ.get("BACKBONE_ENDPOINT_URL", "")
_RECENT_DAYS = 30

_SIGNAL_WEIGHTS = {
    "opened":  1,
    "clicked": 3,
    "replied": 10,
}


def _is_recent(date_str: str, cutoff: datetime) -> bool:
    if not date_str:
        return False
    try:
        dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except ValueError:
        return False


def _score_entries(entries: list[dict], cutoff: datetime) -> int:
    recent = old = 0
    for entry in entries:
        sent_at = entry.get("sent_at", "")
        is_recent = _is_recent(sent_at, cutoff)

        weight = 0
        if entry.get("replied", "").upper() in ("TRUE", "1", "YES"):
            weight = _SIGNAL_WEIGHTS["replied"]
        elif entry.get("clicked", "").upper() in ("TRUE", "1", "YES"):
            weight = _SIGNAL_WEIGHTS["clicked"]
        elif entry.get("opened", "").upper() in ("TRUE", "1", "YES"):
            weight = _SIGNAL_WEIGHTS["opened"]

        if is_recent:
            recent += weight
        else:
            old += weight

    return int(recent + 0.5 * old)


def run() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_RECENT_DAYS)

    contacts_raw = read_range(_SHEET_ID, "Contacts!A:N")
    contacts = rows_to_dicts(contacts_raw)

    log_raw = read_range(_SHEET_ID, "Outreach Log!A:L")
    log_rows = rows_to_dicts(log_raw)

    log_by_contact: dict[str, list[dict]] = {}
    for row in log_rows:
        cid = row.get("contact_id", "")
        if cid:
            log_by_contact.setdefault(cid, []).append(row)

    updated = 0
    for contact in contacts:
        cid = contact.get("id", "")
        if not cid:
            continue
        if contact.get("status", "").lower() == "opted out":
            continue

        entries = log_by_contact.get(cid, [])
        new_score = _score_entries(entries, cutoff)

        if str(contact.get("intent_score", "")) == str(new_score):
            continue

        _post_backbone("adjustIntentScoreDirect", {
            "contact_id": cid,
            "intent_score": new_score,
        })
        updated += 1

    print(f"Intent decay complete — {updated} contacts updated.")


def _post_backbone(action: str, data: dict) -> None:
    if not _BACKBONE_URL:
        print(f"[WARN] BACKBONE_ENDPOINT_URL not set; skipping {action} for {data}")
        return
    resp = requests.post(_BACKBONE_URL, json={"action": action, "data": data}, timeout=15)
    resp.raise_for_status()


if __name__ == "__main__":
    run()
