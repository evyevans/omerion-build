"""One-shot bootstrap for the CRM spreadsheet.

    python -m infra.google.bootstrap_sheet

Idempotent: creates any missing tabs + writes canonical headers. Safe to
re-run after adding new columns.
"""
from __future__ import annotations

from omerion_core.clients.google_client import sheets_service
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.infra.google")

TABS: dict[str, list[str]] = {
    "Contacts": [
        "contact_id", "account_id", "full_name", "email", "linkedin_url", "persona",
        "status", "fit_score", "intent_score", "timing_score",
        "last_touch_at", "synced_at",
    ],
    "Accounts": [
        "account_id", "market_id", "name", "domain", "tier", "status",
        "team_size_bucket", "persona", "score", "created_at", "synced_at",
    ],
    "Opportunities": [
        "opportunity_id", "account_id", "primary_contact_id", "deal_stage",
        "value", "playbook_ref", "owner", "created_at", "synced_at",
    ],
    "Tasks": [
        "task_id", "deployment_id", "title", "status",
        "assignee", "pr_url", "created_at", "synced_at",
    ],
    "Review Queue": [
        "review_id", "agent_name", "subject", "context_md",
        "draft_link", "Approve", "Reject",
        "approve_token", "reject_token",
        "created_at", "expires_at", "synced_at",
    ],
    "Outreach Log": [
        "comm_id", "contact_id", "channel", "direction", "subject",
        "body_snippet", "status", "sent_at", "synced_at",
    ],
    "Deployments": [
        "deployment_id", "blueprint_id", "status", "pr_url", "deployed_at",
        "rollback_url", "synced_at",
    ],
    "Daily Digest": [
        "section", "headline", "detail", "score", "link", "generated_at",
    ],
}


def bootstrap() -> None:
    svc = sheets_service()
    sid = settings.google_crm_sheet_id
    if not sid:
        raise RuntimeError("GOOGLE_CRM_SHEET_ID not set")

    meta = svc.spreadsheets().get(spreadsheetId=sid).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    requests = []
    for tab in TABS:
        if tab not in existing:
            requests.append({"addSheet": {"properties": {"title": tab}}})
    if requests:
        svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": requests}).execute()
        log.info("sheets_tabs_created", tabs=[r["addSheet"]["properties"]["title"] for r in requests])

    for tab, headers in TABS.items():
        svc.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"{tab}!A1:{_col(len(headers))}1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()
        log.info("sheets_headers_written", tab=tab, count=len(headers))


def _col(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


if __name__ == "__main__":
    bootstrap()
