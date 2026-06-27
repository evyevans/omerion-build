"""
Daily Digest builder — reads KPIs from Supabase and POSTs a formatted
embed to Discord at 6 PM America/Toronto.

Idempotent: checks if a digest for today was already sent before posting.
Designed to be invoked by a cron job (e.g., systemd timer or Railway cron).

REWIRED: Uses Supabase directly instead of Google Sheets API.
"""
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.discord.digest")

_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "") or os.environ.get("DISCORD_COMPLETION_WEBHOOK_URL", "")
_TORONTO = ZoneInfo("America/Toronto")
_SENT_LOG = Path("tmp/digest_sent.json")


def _today_toronto() -> str:
    return datetime.now(_TORONTO).date().isoformat()


def _already_sent(today: str) -> bool:
    try:
        with open(_SENT_LOG) as f:
            sent = json.load(f)
        return today in sent
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def _mark_sent(today: str) -> None:
    _SENT_LOG.parent.mkdir(exist_ok=True)
    try:
        with open(_SENT_LOG) as f:
            sent = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        sent = {}
    sent[today] = datetime.now(timezone.utc).isoformat()
    with open(_SENT_LOG, "w") as f:
        json.dump(sent, f)


# ── Supabase queries ─────────────────────────────────────────────────────────

def _query_outreach(today: str) -> dict:
    """Count messages sent and replies received today."""
    sent = 0
    replied = 0
    try:
        result = supabase.table("outbound_communications").select(
            "comm_id", count="exact"
        ).gte("sent_at", f"{today}T00:00:00Z").lte(
            "sent_at", f"{today}T23:59:59Z"
        ).execute()
        sent = result.count or 0
    except Exception as exc:
        log.warning("digest_sent_query_error", error=str(exc))

    try:
        result = supabase.table("outbound_communications").select(
            "comm_id", count="exact"
        ).gte("replied_at", f"{today}T00:00:00Z").lte(
            "replied_at", f"{today}T23:59:59Z"
        ).execute()
        replied = result.count or 0
    except Exception as exc:
        log.warning("digest_replied_query_error", error=str(exc))

    return {"sent": sent, "replied": replied}


def _query_opportunities(today: str) -> dict:
    """Count new opportunities today and total pipeline value."""
    new_today = 0
    pipeline_total = 0.0
    try:
        result = supabase.table("opportunities").select(
            "opportunity_id", count="exact"
        ).gte("created_at", f"{today}T00:00:00Z").lte(
            "created_at", f"{today}T23:59:59Z"
        ).execute()
        new_today = result.count or 0
    except Exception as exc:
        log.warning("digest_new_opps_error", error=str(exc))

    try:
        result = supabase.table("opportunities").select(
            "value,deal_stage"
        ).neq("deal_stage", "Closed Lost").execute()
        for row in (result.data or []):
            try:
                pipeline_total += float(row.get("value", 0) or 0)
            except (ValueError, TypeError):
                pass
    except Exception as exc:
        log.warning("digest_pipeline_error", error=str(exc))

    return {"new_today": new_today, "pipeline_total": pipeline_total}


def _query_tasks_pending() -> int:
    """Count open tasks."""
    try:
        result = supabase.table("tasks").select(
            "task_id", count="exact"
        ).in_("status", ["open", "in_progress"]).execute()
        return result.count or 0
    except Exception as exc:
        log.warning("digest_tasks_error", error=str(exc))
        return 0


def _query_opted_out_today(today: str) -> int:
    """Count contacts opted out today."""
    try:
        result = supabase.table("contacts").select(
            "contact_id", count="exact"
        ).eq("do_not_contact", True).gte(
            "updated_at", f"{today}T00:00:00Z"
        ).lte("updated_at", f"{today}T23:59:59Z").execute()
        return result.count or 0
    except Exception as exc:
        log.warning("digest_optout_error", error=str(exc))
        return 0


def _query_llm_cost(today: str) -> float:
    """Sum LLM cost for today from agent_runs table."""
    try:
        result = supabase.table("agent_runs").select(
            "llm_cost_usd"
        ).gte("started_at", f"{today}T00:00:00Z").lte(
            "started_at", f"{today}T23:59:59Z"
        ).execute()
        total = 0.0
        for row in (result.data or []):
            try:
                total += float(row.get("llm_cost_usd", 0) or 0)
            except (ValueError, TypeError):
                pass
        return total
    except Exception as exc:
        log.warning("digest_cost_error", error=str(exc))
        return 0.0


# ── Embed builder ─────────────────────────────────────────────────────────────

def _build_embed(today: str, kpis: dict) -> dict:
    sent = kpis["sent"]
    replied = kpis["replied"]
    reply_rate = f"{(replied / sent * 100):.1f}%" if sent else "—"
    pipeline = kpis["pipeline_total"]
    cost = kpis["llm_cost"]
    cost_per_lead = cost / sent if sent else 0.0

    return {
        "embeds": [{
            "title": f"🟢 OMERION Daily Digest — {today}",
            "color": 0x00C853,
            "fields": [
                {
                    "name": "📊 ACTIVITY",
                    "value": (
                        f"Messages Sent: **{sent}**\n"
                        f"Replies Received: **{replied}** ({reply_rate} reply rate)\n"
                        f"Meetings Booked: **{kpis.get('meetings', 0)}**"
                    ),
                    "inline": False,
                },
                {
                    "name": "💰 PIPELINE",
                    "value": (
                        f"New Opportunities: **{kpis['new_opps']}**\n"
                        f"Total Pipeline Value: **${pipeline:,.0f}**"
                    ),
                    "inline": False,
                },
                {
                    "name": "⚠️ FLAGS",
                    "value": (
                        f"Opted Out Today: **{kpis['opted_out_today']}**\n"
                        f"Tasks Pending: **{kpis['tasks_pending']}**"
                    ),
                    "inline": False,
                },
                {
                    "name": "💸 COST",
                    "value": (
                        f"LLM Spend Today: **${cost:.2f}**\n"
                        f"Cost Per Lead: **${cost_per_lead:.2f}**"
                    ),
                    "inline": False,
                },
            ],
            "footer": {"text": "Omerion AI • Auto-generated"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    today = _today_toronto()

    if _already_sent(today):
        log.info("digest_already_sent", date=today)
        return

    if not _WEBHOOK_URL:
        raise EnvironmentError("DISCORD_WEBHOOK_URL is not set.")

    outreach = _query_outreach(today)
    opps = _query_opportunities(today)
    tasks_pending = _query_tasks_pending()
    opted_out = _query_opted_out_today(today)
    llm_cost = _query_llm_cost(today)

    kpis = {
        **outreach,
        "new_opps": opps["new_today"],
        "pipeline_total": opps["pipeline_total"],
        "opted_out_today": opted_out,
        "tasks_pending": tasks_pending,
        "llm_cost": llm_cost,
    }

    payload = _build_embed(today, kpis)
    resp = requests.post(_WEBHOOK_URL, json=payload, timeout=10)
    resp.raise_for_status()

    _mark_sent(today)
    log.info("digest_sent", date=today, kpis=kpis)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
