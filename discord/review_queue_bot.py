"""
Review Queue Discord bot — polls Supabase founder_review_queue every 5 minutes
for rows with status = "pending" and sends interactive Approve/Reject messages.

Button clicks update the review row directly via Supabase.
Duplicate notifications are prevented via a local seen-set (tmp/rq_notified.json).

REWIRED: Uses Supabase directly instead of Google Sheets API + HTTP calls.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
from dotenv import load_dotenv

# ── Path bootstrap: allow imports from omerion/ package ──────────────────────
_repo_root = Path(__file__).parent.parent
_omerion_pkg = _repo_root / "omerion"
if str(_omerion_pkg) not in sys.path:
    sys.path.insert(0, str(_omerion_pkg))

# Load environment variables
load_dotenv(_repo_root / "discord" / ".env")
load_dotenv(_repo_root / "omerion" / ".env")

import discord
from discord.ext import tasks
from discord import ButtonStyle
from discord.ui import Button, View

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger

log = get_logger("omerion.discord.review_bot")

_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
_HITL_CHANNEL_ID = int(os.environ.get("DISCORD_HITL_CHANNEL_ID", "0"))
_NOTIFIED_LOG = Path("tmp/rq_notified.json")

intents = discord.Intents.default()
bot = discord.Client(intents=intents)


# ── Notified cache ────────────────────────────────────────────────────────────

def _load_notified() -> set:
    try:
        data = json.loads(_NOTIFIED_LOG.read_text())
        return set(data)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_notified(seen: set) -> None:
    _NOTIFIED_LOG.parent.mkdir(exist_ok=True)
    _NOTIFIED_LOG.write_text(json.dumps(list(seen)))


# ── Supabase operations ──────────────────────────────────────────────────────

def _fetch_pending_reviews() -> list[dict]:
    """Fetch all pending review items from Supabase."""
    try:
        result = supabase.table("founder_review_queue").select(
            "review_id,agent_name,subject,context_md,draft_ref,created_at"
        ).eq("decision", "pending").order("created_at", desc=False).execute()
        return result.data or []
    except Exception as exc:
        log.error("review_queue_fetch_error", error=str(exc))
        return []


def _update_review_status(review_id: str, decision: str, reason: str = "") -> None:
    """Update a review item's status in Supabase."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        supabase.table("founder_review_queue").update({
            "decision": decision,
            "decision_notes": reason,
            "decided_at": now,
        }).eq("review_id", review_id).execute()
        log.info("review_status_updated", review_id=review_id, status=decision)
    except Exception as exc:
        log.error("review_update_error", review_id=review_id, error=str(exc))


def _get_contact_name(review: dict) -> str:
    """Fallback contact name from context."""
    return "See context below"


# ── Discord UI ────────────────────────────────────────────────────────────────

class ReviewView(View):
    def __init__(self, review: dict, contact_name: str):
        super().__init__(timeout=None)
        self.review = review
        self.contact_name = contact_name

    @discord.ui.button(label="✅ Approve", style=ButtonStyle.success, custom_id="approve")
    async def approve(self, interaction: discord.Interaction, button: Button):
        _update_review_status(self.review["review_id"], "approved", "Founder approved via Discord")
        await interaction.response.edit_message(
            content=f"✅ **Approved** — {self.contact_name} cleared for send.",
            view=None,
        )

    @discord.ui.button(label="❌ Reject", style=ButtonStyle.danger, custom_id="reject")
    async def reject(self, interaction: discord.Interaction, button: Button):
        _update_review_status(self.review["review_id"], "rejected", "Founder rejected via Discord")
        await interaction.response.edit_message(
            content=f"❌ **Rejected** — draft for {self.contact_name} discarded.",
            view=None,
        )

    @discord.ui.button(label="👀 View Full", style=ButtonStyle.secondary, custom_id="view_full")
    async def view_full(self, interaction: discord.Interaction, button: Button):
        context = self.review.get("context_md", "(no context)")
        await interaction.response.send_message(
            f"```\n{context[:1900]}\n```",
            ephemeral=True,
        )


def _build_message(review: dict, contact_name: str) -> tuple[str, ReviewView]:
    subject = review.get("subject", "(no subject)")
    agent = review.get("agent_name", "system")
    context = review.get("context_md", "")
    preview = context[:200] + "..." if len(context) > 200 else context

    text = (
        f"📨 **REVIEW NEEDED**\n\n"
        f"**Contact:** {contact_name}\n"
        f"**Agent:** {agent}\n"
        f"**Subject:** {subject}\n\n"
        f"**Preview:**\n{preview}"
    )
    return text, ReviewView(review, contact_name)


# ── Polling loop ──────────────────────────────────────────────────────────────

@tasks.loop(minutes=5)
async def poll_review_queue():
    channel = bot.get_channel(_HITL_CHANNEL_ID)
    if channel is None:
        log.error("hitl_channel_not_found", channel_id=_HITL_CHANNEL_ID)
        return

    reviews = _fetch_pending_reviews()
    seen = _load_notified()
    new_seen = set(seen)

    for review in reviews:
        review_id = review.get("review_id", "")
        if not review_id or review_id in seen:
            continue

        contact_name = _get_contact_name(review)
        text, view = _build_message(review, contact_name)

        try:
            await channel.send(content=text, view=view)
            new_seen.add(review_id)
        except discord.DiscordException as exc:
            log.error("review_send_error", review_id=review_id, error=str(exc))

    if new_seen != seen:
        _save_notified(new_seen)


@bot.event
async def on_ready():
    log.info("review_bot_online", user=str(bot.user))
    poll_review_queue.start()


def run():
    if not _BOT_TOKEN:
        raise EnvironmentError("DISCORD_BOT_TOKEN is not set.")
    bot.run(_BOT_TOKEN)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
