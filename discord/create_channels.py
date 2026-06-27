"""Idempotently provision Omerion's Discord channels.

Reads `DISCORD_BOT_TOKEN` + `DISCORD_GUILD_ID` from env (same as omerion_bot.py)
and creates any channels listed in `CHANNEL_LAYOUT` that don't already exist.
Skips existing channels by name. Creates the category if it doesn't exist.

Run from the repo root:
    python discord/create_channels.py

Required env (loaded from discord/.env and/or omerion/.env via dotenv):
    DISCORD_BOT_TOKEN
    DISCORD_GUILD_ID
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv

_repo_root = Path(__file__).parent.parent
load_dotenv(_repo_root / "discord" / ".env")
load_dotenv(_repo_root / "omerion" / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("create_channels")

# Category name → list of channel names to ensure exist inside it.
# Must stay in sync with CHANNEL_SKILL_MAP in omerion_core/inbound/discord_route.py.
#
# LEAD GEN    — market discovery → contact sourcing → scoring pipeline
# OUTREACH    — nurture, LinkedIn, biz dev, offer matching
# RESEARCH    — market watching, OSS scouting, architecture, eval, competitive
# DELIVERY    — meeting intel, build orchestration, attribution, onboarding, success
# SYSTEM      — read-only bot channels (HITL approvals, narration, daily digest)
CHANNEL_LAYOUT: dict[str, list[str]] = {
    "LEAD GEN": ["map", "scout", "leads", "score"],
    "OUTREACH": ["nurture", "reach", "biz", "match"],
    "RESEARCH": ["watch", "oss", "arch", "eval", "compete"],
    "DELIVERY": ["intel", "orch", "attrib", "onboard", "success"],
    "SYSTEM": ["founder-hitl", "mission-control", "omerion-room"],
}


async def _ensure_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    for cat in guild.categories:
        if cat.name.upper() == name.upper():
            return cat
    log.info("creating_category", extra={"name": name})
    return await guild.create_category(name=name)


async def _ensure_text_channel(
    guild: discord.Guild, category: discord.CategoryChannel, channel_name: str
) -> None:
    existing_by_name = {c.name.lower(): c for c in guild.text_channels}
    if channel_name.lower() in existing_by_name:
        existing = existing_by_name[channel_name.lower()]
        if existing.category_id != category.id:
            log.info(
                "channel_exists_wrong_category",
                extra={"channel": channel_name, "current_category": existing.category.name if existing.category else None, "target_category": category.name},
            )
        else:
            log.info("channel_already_present", extra={"channel": channel_name})
        return
    log.info("creating_channel", extra={"channel": channel_name, "category": category.name})
    await guild.create_text_channel(name=channel_name, category=category)


async def main() -> int:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    guild_id_str = os.environ.get("DISCORD_GUILD_ID")
    if not token or not guild_id_str:
        log.error("missing_env DISCORD_BOT_TOKEN / DISCORD_GUILD_ID")
        return 2
    guild_id = int(guild_id_str)

    intents = discord.Intents.default()
    intents.guilds = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(guild_id) or await client.fetch_guild(guild_id)
            if guild is None:
                log.error("guild_not_found", extra={"guild_id": guild_id})
                await client.close()
                return
            for category_name, channels in CHANNEL_LAYOUT.items():
                category = await _ensure_category(guild, category_name)
                for ch in channels:
                    await _ensure_text_channel(guild, category, ch)
            log.info("provisioning_complete")
        finally:
            await client.close()

    await client.start(token)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
