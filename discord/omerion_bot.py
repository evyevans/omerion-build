"""Omerion first-party Discord bot.

Handles HITL review cards, agent narration, daily digests, and slash commands.
Runs as a single persistent process alongside the FastAPI app.

Usage (from the repo root, with the omerion venv activated):
    python discord/omerion_bot.py

Required env vars: see discord/.env.example
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ── SSL trust-store bootstrap (must run before `import discord`) ──────────────
# The python.org macOS build points OpenSSL at a cert.pem that does not exist
# (Framework .../etc/openssl/cert.pem), so aiohttp — discord.py's HTTP layer —
# gets an empty trust store and every TLS handshake to discord.com fails with
# CERTIFICATE_VERIFY_FAILED. certifi ships a valid CA bundle; point OpenSSL at
# it via the env vars create_default_context() honors. setdefault() means a
# real system/prod cert config (e.g. Railway) is never overridden.
import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("SSL_CERT_DIR", os.path.dirname(certifi.where()))

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

# ── Path bootstrap: allow imports from omerion/ package ──────────────────────
_repo_root = Path(__file__).parent.parent
_omerion_pkg = _repo_root / "omerion"
if str(_omerion_pkg) not in sys.path:
    sys.path.insert(0, str(_omerion_pkg))

# Load environment variables
load_dotenv(_repo_root / "discord" / ".env")
load_dotenv(_repo_root / "omerion" / ".env")

# ── Sibling imports ───────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from http_client import BotAPIError, OmerionClient
from narration import format_event
from views import HITLView

from omerion_core.clients.supabase_client import supabase
from omerion_core.logging import get_logger
from omerion_core.runtime.health_sidecar import (
    heartbeat,
    port_for,
    serve as serve_health,
    set_extra_status,
)

log = get_logger("omerion.discord.bot")

# Guard against multiple sidecar spawns when Discord reconnects (on_ready fires
# again on every reconnect; we want exactly one sidecar HTTP listener).
_sidecar_started = False

# ── Config ────────────────────────────────────────────────────────────────────
# NOTE: required tokens use .get("") (not os.environ[...]) so a missing var does
# NOT crash with a bare KeyError at import. The __main__ block validates them and
# emits a clear fatal message. This is how a misconfigured Railway env surfaces a
# readable error instead of a silent crash-loop.
_BOT_TOKEN       = os.environ.get("DISCORD_BOT_TOKEN", "")
_GUILD_ID        = int(os.environ.get("DISCORD_GUILD_ID", "0"))
_HITL_CH_ID      = int(os.environ.get("DISCORD_HITL_CHANNEL_ID", "0"))
_ROOM_CH_ID      = int(os.environ.get("DISCORD_OMERION_ROOM_ID", "0"))
_MC_CH_ID        = int(os.environ.get("DISCORD_MISSION_CONTROL_ID", "0"))
_API_URL         = os.environ.get("OMERION_API_BASE_URL", "http://localhost:8000")
_API_TOKEN       = os.environ.get("OMERION_WEBHOOK_TOKEN", "")
_TORONTO         = ZoneInfo("America/Toronto")

# Channels the bot NEVER forwards messages from (read-only command centers).
_READ_ONLY_CHANNELS = {"founder-hitl", "mission-control"}

# All 15 agent skill names for slash command autocomplete.
_AGENT_NAMES = [
    "crm-nurture", "lead-scraper", "icp-scoring", "linkedin-outreach",
    "hq-lead-scraping", "offer-matching", "meeting-intel", "market-watcher",
    "oss-scout", "strategic-arch", "build-orchestrator", "outcome-attribution",
    "eval-telemetry", "job-seeker", "market-mapper",
]

# ── Bot + command tree setup ──────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot   = discord.Client(intents=intents)
tree  = app_commands.CommandTree(bot)
api   = OmerionClient(base_url=_API_URL, token=_API_TOKEN)
_guild = discord.Object(id=_GUILD_ID) if _GUILD_ID else None


# ── Error logging to Supabase ─────────────────────────────────────────────────

def _log_error(message: str, *, source: str = "omerion_bot", traceback: str | None = None, **meta) -> None:
    try:
        supabase.table("error_log").insert({
            "source": source,
            "message": message,
            "traceback": traceback,
            "meta": meta,
        }).execute()
    except Exception as exc:
        log.error("error_log_write_failed", error=str(exc))


# ── HITL dedup via agent_messages table ───────────────────────────────────────

def _already_notified(review_id: str) -> bool:
    try:
        r = supabase.table("agent_messages").select("id", count="exact").eq(
            "event_type", "hitl_notification"
        ).eq("from_agent", "system").filter("meta->>review_id", "eq", review_id).execute()
        return (r.count or 0) > 0
    except Exception:
        return False


def _mark_notified(review_id: str) -> None:
    try:
        supabase.table("agent_messages").insert({
            "from_agent": "system",
            "message": f"HITL notification sent for review {review_id}",
            "event_type": "hitl_notification",
            "meta": {"review_id": review_id},
        }).execute()
    except Exception as exc:
        log.error("mark_notified_failed", review_id=review_id, error=str(exc))


# ── Digest dedup ──────────────────────────────────────────────────────────────

def _digest_already_sent(today: str) -> bool:
    try:
        r = supabase.table("agent_messages").select("id", count="exact").eq(
            "event_type", "digest_sent"
        ).eq("from_agent", "system").filter("meta->>date", "eq", today).execute()
        return (r.count or 0) > 0
    except Exception:
        return False


def _mark_digest_sent(today: str) -> None:
    try:
        supabase.table("agent_messages").insert({
            "from_agent": "system",
            "message": f"Daily digest sent for {today}",
            "event_type": "digest_sent",
            "meta": {"date": today},
        }).execute()
    except Exception as exc:
        log.error("mark_digest_sent_failed", date=today, error=str(exc))


# ── Digest KPI queries ────────────────────────────────────────────────────────

def _kpi_outreach(today: str) -> dict:
    sent = replied = 0
    try:
        r = supabase.table("outbound_communications").select("comm_id", count="exact").gte(
            "sent_at", f"{today}T00:00:00Z"
        ).lte("sent_at", f"{today}T23:59:59Z").execute()
        sent = r.count or 0
    except Exception:
        pass
    try:
        r = supabase.table("outbound_communications").select("comm_id", count="exact").gte(
            "replied_at", f"{today}T00:00:00Z"
        ).lte("replied_at", f"{today}T23:59:59Z").execute()
        replied = r.count or 0
    except Exception:
        pass
    return {"sent": sent, "replied": replied}


def _kpi_pipeline(today: str) -> dict:
    new_today = 0
    total = 0.0
    try:
        r = supabase.table("opportunities").select("opportunity_id", count="exact").gte(
            "created_at", f"{today}T00:00:00Z"
        ).lte("created_at", f"{today}T23:59:59Z").execute()
        new_today = r.count or 0
    except Exception:
        pass
    try:
        r = supabase.table("opportunities").select("value,deal_stage").neq(
            "deal_stage", "Closed Lost"
        ).execute()
        for row in r.data or []:
            try:
                total += float(row.get("value", 0) or 0)
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    return {"new_opps": new_today, "pipeline_total": total}


def _kpi_tasks() -> int:
    try:
        r = supabase.table("tasks").select("task_id", count="exact").in_(
            "status", ["open", "in_progress"]
        ).execute()
        return r.count or 0
    except Exception:
        return 0


def _kpi_opted_out(today: str) -> int:
    try:
        r = supabase.table("contacts").select("contact_id", count="exact").eq(
            "do_not_contact", True
        ).gte("updated_at", f"{today}T00:00:00Z").lte("updated_at", f"{today}T23:59:59Z").execute()
        return r.count or 0
    except Exception:
        return 0


def _kpi_cost(today: str) -> float:
    try:
        r = supabase.table("agent_runs").select("llm_cost_usd").gte(
            "started_at", f"{today}T00:00:00Z"
        ).lte("started_at", f"{today}T23:59:59Z").execute()
        total = 0.0
        for row in r.data or []:
            try:
                total += float(row.get("llm_cost_usd", 0) or 0)
            except (ValueError, TypeError):
                pass
        return total
    except Exception:
        return 0.0


def _kpi_hitl_pending() -> int:
    try:
        r = supabase.table("founder_review_queue").select("review_id", count="exact").eq(
            "status", "pending"
        ).execute()
        return r.count or 0
    except Exception:
        return 0


def _build_digest_embed(today: str) -> dict:
    outreach    = _kpi_outreach(today)
    pipeline    = _kpi_pipeline(today)
    tasks_open  = _kpi_tasks()
    opted_out   = _kpi_opted_out(today)
    cost        = _kpi_cost(today)
    hitl_wait   = _kpi_hitl_pending()

    sent       = outreach["sent"]
    replied    = outreach["replied"]
    reply_rate = f"{(replied/sent*100):.1f}%" if sent else "—"
    cost_per   = cost / sent if sent else 0.0

    return {
        "title": f"🟢 OMERION Daily Digest — {today}",
        "color": 0x00C853,
        "fields": [
            {
                "name": "📊 ACTIVITY",
                "value": (
                    f"Messages Sent: **{sent}**\n"
                    f"Replies: **{replied}** ({reply_rate})\n"
                    f"New Opportunities: **{pipeline['new_opps']}**"
                ),
                "inline": False,
            },
            {
                "name": "💰 PIPELINE",
                "value": f"Total Pipeline: **${pipeline['pipeline_total']:,.0f}**",
                "inline": True,
            },
            {
                "name": "⏸️ HITL",
                "value": f"Pending Approvals: **{hitl_wait}**",
                "inline": True,
            },
            {
                "name": "⚠️ FLAGS",
                "value": (
                    f"Opted Out Today: **{opted_out}**\n"
                    f"Tasks Pending: **{tasks_open}**"
                ),
                "inline": False,
            },
            {
                "name": "💸 COST",
                "value": (
                    f"LLM Spend: **${cost:.2f}**\n"
                    f"Cost/Lead: **${cost_per:.2f}**"
                ),
                "inline": False,
            },
        ],
        "footer": {"text": "Omerion AI • Auto-generated"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Narration watermark ───────────────────────────────────────────────────────
_last_event_ts: datetime = datetime.now(timezone.utc)


# ── Periodic tasks ────────────────────────────────────────────────────────────

@tasks.loop(seconds=60)
async def poll_hitl_queue() -> None:
    """Fetch pending HITL reviews and post cards to #founder-hitl."""
    heartbeat()  # health sidecar liveness
    channel = bot.get_channel(_HITL_CH_ID)
    if channel is None:
        return
    try:
        reviews = await api.get_pending_hitl()
    except BotAPIError as exc:
        log.error("hitl_poll_failed", error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — network/timeout errors must not crash the loop
        log.warning("hitl_poll_transient_error", error=str(exc), error_class=type(exc).__name__)
        return

    for review in reviews:
        review_id = review.get("review_id", "")
        if not review_id or _already_notified(review_id):
            continue

        view = HITLView(
            review_id=review_id,
            agent_name=review.get("agent_name", "system"),
            subject=review.get("subject", ""),
            approve_token=review.get("approve_token", ""),
            reject_token=review.get("reject_token", ""),
            client=api,
        )
        embed = discord.Embed(
            title="📨 REVIEW NEEDED",
            description=(
                f"**Agent:** {review.get('agent_name','')}\n"
                f"**Subject:** {review.get('subject','')}\n\n"
                f"*Created: {review.get('created_at','')[:19]}*"
            ),
            color=0xF1C40F,
            timestamp=datetime.now(timezone.utc),
        )
        try:
            await channel.send(embed=embed, view=view)
            _mark_notified(review_id)
            log.info("hitl_card_sent", review_id=review_id)
        except discord.DiscordException as exc:
            log.error("hitl_send_failed", review_id=review_id, error=str(exc))


@tasks.loop(seconds=60)
async def poll_narration() -> None:
    """Poll events table for new rows and post narration lines to #omerion-room."""
    global _last_event_ts
    channel = bot.get_channel(_ROOM_CH_ID)
    if channel is None:
        return

    cutoff = _last_event_ts.isoformat()
    try:
        result = supabase.table("events").select(
            "event_id,type,payload,source_agent,created_at"
        ).gt("created_at", cutoff).order("created_at").limit(50).execute()
        rows = result.data or []
    except Exception as exc:
        log.error("narration_poll_failed", error=str(exc))
        return

    for row in rows:
        created = row.get("created_at", "")
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if ts > _last_event_ts:
                _last_event_ts = ts
        except (ValueError, TypeError):
            pass

        payload = row.get("payload") or {}
        payload["source_agent"] = row.get("source_agent", "")
        line = format_event(row.get("type", ""), payload)
        if not line:
            continue
        try:
            await channel.send(line)
        except discord.DiscordException as exc:
            log.error("narration_send_failed", error=str(exc))


@tasks.loop(seconds=30)
async def daily_digest_check() -> None:
    """Post daily digest at 18:00 America/Toronto. Idempotent."""
    now_toronto = datetime.now(_TORONTO)
    if now_toronto.hour != 18 or now_toronto.minute > 2:
        return

    today = now_toronto.date().isoformat()
    if _digest_already_sent(today):
        return

    channel = bot.get_channel(_MC_CH_ID)
    if channel is None:
        return

    embed_data = _build_digest_embed(today)
    embed = discord.Embed.from_dict(embed_data)
    try:
        await channel.send(embed=embed)
        _mark_digest_sent(today)
        log.info("digest_sent", date=today)
    except discord.DiscordException as exc:
        log.error("digest_send_failed", date=today, error=str(exc))


# ── Periodic-task error handlers ──────────────────────────────────────────────
# discord.ext.tasks.loop stops a task when an unhandled exception escapes the
# loop body. The body-level try/excepts above catch the documented httpx /
# Supabase failure modes, but a previously-unseen exception (heartbeat, embed
# construction, view binding) could still silently kill a loop and stop ALL
# downstream behaviour invisibly — HITL approvals stop arriving, narration
# goes dead, digest never posts. These handlers log the failure and restart
# the loop so a single transient error never disables a core surface.

@poll_hitl_queue.error
async def _poll_hitl_queue_error(exc: Exception) -> None:
    log.exception("hitl_poll_loop_died", error=str(exc), error_class=type(exc).__name__)
    if not poll_hitl_queue.is_running():
        poll_hitl_queue.restart()


@poll_narration.error
async def _poll_narration_error(exc: Exception) -> None:
    log.exception("narration_poll_loop_died", error=str(exc), error_class=type(exc).__name__)
    if not poll_narration.is_running():
        poll_narration.restart()


@daily_digest_check.error
async def _daily_digest_check_error(exc: Exception) -> None:
    log.exception("daily_digest_loop_died", error=str(exc), error_class=type(exc).__name__)
    if not daily_digest_check.is_running():
        daily_digest_check.restart()


# ── Message routing ───────────────────────────────────────────────────────────

# Audio MIME types Discord attaches to voice memos (matches the backend's
# _SUPPORTED_AUDIO_TYPES in omerion_core/inbound/discord_voice.py).
_AUDIO_CONTENT_TYPES = {
    "audio/ogg", "audio/mpeg", "audio/mp3", "audio/mp4",
    "audio/wav", "audio/webm", "audio/x-m4a",
}
_AUDIO_EXTENSIONS = (".ogg", ".mp3", ".mpeg", ".mp4", ".m4a", ".wav", ".webm")


def _first_audio_attachment(message: discord.Message):
    """Return the first audio attachment on a message, or None.

    Discord native voice messages set ``content_type`` to ``audio/ogg`` and
    ``is_voice_message()`` True; uploaded audio files are matched by extension
    as a fallback when content_type is missing.
    """
    for att in message.attachments:
        ctype = (att.content_type or "").lower().split(";")[0].strip()
        if ctype in _AUDIO_CONTENT_TYPES:
            return att
        if getattr(att, "is_voice_message", None) and att.is_voice_message():
            return att
        if (att.filename or "").lower().endswith(_AUDIO_EXTENSIONS):
            return att
    return None


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if not message.guild:
        return
    channel_name = message.channel.name if hasattr(message.channel, "name") else ""
    if channel_name in _READ_ONLY_CHANNELS:
        return

    guild_id     = str(message.guild.id)
    author       = str(message.author)
    channel_id   = str(message.channel.id)
    thread_id    = str(message.channel.id) if isinstance(message.channel, discord.Thread) else None

    # Voice memo? Download the audio attachment and route it through Whisper
    # transcription instead of the plain text path. The backend injects the
    # transcript into the same agent run as if it had been typed.
    audio = _first_audio_attachment(message)
    if audio is not None:
        try:
            audio_bytes = await audio.read()
            result = await api.route_discord_voice(
                channel_name=channel_name,
                guild_id=guild_id,
                author=author,
                audio_bytes=audio_bytes,
                filename=audio.filename or "voice.ogg",
                content_type=(audio.content_type or "audio/ogg").split(";")[0].strip(),
                discord_channel_id=channel_id,
                discord_thread_id=thread_id,
            )
        except BotAPIError as exc:
            log.error("voice_route_failed", channel=channel_name, error=str(exc))
            _log_error(f"Discord voice route failed in #{channel_name}: {exc}", source="omerion_bot")
            try:
                await message.reply(
                    "⚠️ Couldn't process that voice note. Try again, or type your message.",
                    mention_author=False,
                )
            except discord.DiscordException:
                pass
            return

        reply = result.get("reply")
        if not reply and result.get("routed"):
            run_id = result.get("run_id", "")
            skill  = result.get("skill", channel_name)
            reply  = f"⚙️ Queued **{skill}** — run `{run_id[:8]}`. I'll post results here."
        if reply:
            try:
                await message.reply(reply, mention_author=False)
            except discord.DiscordException:
                pass
        return

    try:
        result = await api.route_discord_message(
            channel_name=channel_name,
            guild_id=guild_id,
            author=author,
            message=message.content,
            discord_channel_id=channel_id,
            discord_thread_id=thread_id,
        )
    except BotAPIError as exc:
        log.error("route_failed", channel=channel_name, error=str(exc))
        _log_error(f"Discord route failed in #{channel_name}: {exc}", source="omerion_bot")
        # Tell the user instead of failing silently. The old bare `return` is what
        # made a dead backend / bad token look like "the bot is ignoring me".
        try:
            await message.reply(
                "⚠️ Couldn't reach the agent backend — your request wasn't queued. "
                "It's been logged; please retry shortly.",
                mention_author=False,
            )
        except discord.DiscordException:
            pass
        return

    if result.get("routed"):
        run_id = result.get("run_id", "")
        skill  = result.get("skill", channel_name)
        reply  = result.get("reply") or f"⚙️ Queued **{skill}** — run `{run_id[:8]}`. I'll post results here."
        try:
            await message.reply(reply, mention_author=False)
        except discord.DiscordException:
            pass


# ── Slash commands ────────────────────────────────────────────────────────────

async def _agent_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=a, value=a)
        for a in _AGENT_NAMES
        if current.lower() in a.lower()
    ][:25]


@tree.command(name="run", description="Trigger an Omerion agent", guild=_guild)
@app_commands.describe(agent="Agent skill name", input="Optional freeform input for the agent")
@app_commands.autocomplete(agent=_agent_autocomplete)
async def cmd_run(interaction: discord.Interaction, agent: str, input: str | None = None) -> None:
    await interaction.response.defer(ephemeral=False)
    try:
        result = await api.run_agent(
            agent,
            inputs={"message": input} if input else None,
            discord_channel_id=str(interaction.channel_id),
            triggered_by=str(interaction.user),
        )
        run_id = result.get("run_id", "")
        embed = discord.Embed(
            title=f"⚙️ {agent} queued",
            description=f"Run `{run_id[:8]}` started. Results will post when complete.",
            color=0x5865F2,
        )
        await interaction.followup.send(embed=embed)
    except BotAPIError as exc:
        await interaction.followup.send(f"❌ Failed to run `{agent}`: {exc}", ephemeral=True)


@tree.command(name="status", description="Agent performance rollup (14 days)", guild=_guild)
@app_commands.describe(agent="Filter to a specific agent (optional)")
@app_commands.autocomplete(agent=_agent_autocomplete)
async def cmd_status(interaction: discord.Interaction, agent: str | None = None) -> None:
    await interaction.response.defer()
    try:
        data = await api.get_status_rollup()
        agents = data.get("agents", [])
        if agent:
            agents = [a for a in agents if agent.lower() in a.get("agent_name", "").lower()]

        lines = []
        for a in agents[:15]:
            name    = a.get("agent_name", "?")
            runs    = a.get("runs_14d", 0)
            success = a.get("success_rate", 0)
            cost    = a.get("cost_per_run_usd", 0)
            lines.append(f"`{name:<24}` {runs:>4} runs · {success*100:.0f}% ok · ${cost:.2f}/run")

        embed = discord.Embed(
            title=f"📊 Agent Status (14d){' — ' + agent if agent else ''}",
            description="\n".join(lines) or "No data.",
            color=0x2ECC71,
            timestamp=datetime.fromisoformat(data.get("as_of", datetime.now(timezone.utc).isoformat())),
        )
        await interaction.followup.send(embed=embed)
    except BotAPIError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)


@tree.command(name="pending", description="List pending HITL approvals", guild=_guild)
async def cmd_pending(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    try:
        reviews = await api.get_pending_hitl()
        if not reviews:
            await interaction.followup.send("✅ No pending approvals.", ephemeral=True)
            return
        lines = []
        for i, r in enumerate(reviews[:10], 1):
            lines.append(
                f"**{i}.** `{r.get('review_id','')[:8]}` — "
                f"**{r.get('agent_name','')}** — {r.get('subject','')[:60]}"
            )
        embed = discord.Embed(
            title=f"⏸️ {len(reviews)} Pending Approval(s)",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        await interaction.followup.send(embed=embed)
    except BotAPIError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)


@tree.command(name="digest", description="Post today's activity digest", guild=_guild)
async def cmd_digest(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    today = datetime.now(_TORONTO).date().isoformat()
    embed_data = _build_digest_embed(today)
    embed = discord.Embed.from_dict(embed_data)
    await interaction.followup.send(embed=embed)


@tree.command(name="cost", description="Weekly cost report by agent", guild=_guild)
async def cmd_cost(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    try:
        data = await api.get_cost_report()
        week  = data.get("week_total_usd", 0)
        prior = data.get("prior_week_total_usd", 0)
        delta = week - prior
        delta_str = (f"▲ ${delta:.2f}" if delta >= 0 else f"▼ ${abs(delta):.2f}")

        lines = [f"**This week:** ${week:.2f}  ({delta_str} vs last week)\n"]
        for a in (data.get("agents") or [])[:12]:
            lines.append(
                f"`{a.get('agent_name',''):<24}` ${a.get('total_cost_usd',0):.2f} "
                f"({a.get('runs_total',0)} runs · ${a.get('avg_cost_usd',0):.2f}/run)"
            )
        embed = discord.Embed(
            title="💸 Cost Report", description="\n".join(lines), color=0xE74C3C
        )
        await interaction.followup.send(embed=embed)
    except BotAPIError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)


@tree.command(name="pipeline", description="Business pipeline snapshot", guild=_guild)
async def cmd_pipeline(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    try:
        data = await api.get_pipeline_snapshot()
        stages = data.get("opportunities_by_stage", {})
        stage_lines = "\n".join(f"  {k}: **{v}**" for k, v in stages.items())
        embed = discord.Embed(
            title="🏗️ Pipeline Snapshot",
            color=0x3498DB,
        )
        embed.add_field(
            name="Accounts",
            value=f"Total: **{data.get('accounts_total',0)}** · New 24h: **{data.get('accounts_new_24h',0)}**",
            inline=False,
        )
        embed.add_field(name="Opportunities by Stage", value=stage_lines or "—", inline=False)
        embed.add_field(
            name="Clients / Deployments",
            value=(
                f"Active Clients: **{data.get('active_clients',0)}**\n"
                f"Live Deployments: **{data.get('deployments_live',0)}**\n"
                f"Pending: **{data.get('deployments_pending',0)}**"
            ),
            inline=False,
        )
        embed.add_field(name="⏸️ HITL Waiting", value=str(data.get("pending_reviews", 0)), inline=True)
        await interaction.followup.send(embed=embed)
    except BotAPIError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)


@tree.command(name="cancel", description="Cancel a running agent session", guild=_guild)
@app_commands.describe(run_id="Run ID (from a previous /run response)")
async def cmd_cancel(interaction: discord.Interaction, run_id: str) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        result = await api.cancel_session(run_id)
        cancelled = result.get("cancelled", False)
        note = result.get("note", "")
        if cancelled:
            await interaction.followup.send(f"🚫 Session `{run_id[:8]}` cancelled.", ephemeral=True)
        else:
            await interaction.followup.send(
                f"⚠️ Could not cancel `{run_id[:8]}`. {note}", ephemeral=True
            )
    except BotAPIError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)


@tree.command(name="pause", description="Pause an agent's scheduled runs", guild=_guild)
@app_commands.autocomplete(agent=_agent_autocomplete)
async def cmd_pause(interaction: discord.Interaction, agent: str) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        result = await api.pause_agent(agent)
        note = result.get("note", "")
        await interaction.followup.send(
            f"⏸️ **{agent}** paused.{' ' + note if note else ''}", ephemeral=True
        )
    except BotAPIError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)


@tree.command(name="resume", description="Resume a paused agent", guild=_guild)
@app_commands.autocomplete(agent=_agent_autocomplete)
async def cmd_resume(interaction: discord.Interaction, agent: str) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        result = await api.resume_agent(agent)
        note = result.get("note", "")
        await interaction.followup.send(
            f"▶️ **{agent}** resumed.{' ' + note if note else ''}", ephemeral=True
        )
    except BotAPIError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    global _sidecar_started
    log.info("omerion_bot_online", user=str(bot.user))

    if _guild:
        tree.copy_global_to(guild=_guild)
        await tree.sync(guild=_guild)
    else:
        await tree.sync()
    log.info("slash_commands_synced")

    if not _sidecar_started:
        # Health sidecar — exposes /health on $BOT_HEALTH_PORT (default 8002)
        # so Railway can detect a hung bot container. heartbeat() is invoked
        # by the polling loops below to publish liveness.
        set_extra_status(lambda: {"discord_connected": bot.is_ready()})
        asyncio.create_task(serve_health("bot", port=port_for("bot")))
        # Announce online ONCE per process (this block runs once; on_ready fires on
        # every reconnect). Gives the founder a positive signal the bot is live and
        # forwarding — the missing counterpart to the new bot-down watchdog.
        try:
            mc = bot.get_channel(_MC_CH_ID) if _MC_CH_ID else None
            if mc is not None:
                await mc.send(
                    f"✅ **Omerion bot online** — Discord channel triggers are live "
                    f"(backend: `{_API_URL}`)."
                )
        except discord.DiscordException as exc:
            log.warning("online_announce_failed", error=str(exc))
        _sidecar_started = True
        log.info("health_sidecar_started", port=port_for("bot"))

    heartbeat()  # initial tick on connect/reconnect
    poll_hitl_queue.start()
    poll_narration.start()
    daily_digest_check.start()
    log.info("background_tasks_started")


@bot.event
async def on_disconnect() -> None:
    _log_error("Bot disconnected from Discord", source="omerion_bot")
    log.warning("bot_disconnected")


@bot.event
async def on_error(event: str, *args, **kwargs) -> None:
    import traceback
    tb = traceback.format_exc()
    log.error("bot_event_error", event=event, traceback=tb[:500])
    _log_error(f"Discord event error in {event}", source="omerion_bot", traceback=tb)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not _BOT_TOKEN:
        raise EnvironmentError("DISCORD_BOT_TOKEN is required")
    if not _API_TOKEN:
        raise EnvironmentError("OMERION_WEBHOOK_TOKEN is required")
    try:
        bot.run(_BOT_TOKEN, log_handler=None)
    except discord.errors.LoginFailure:
        # A 401 here means the token is wrong, reset, or revoked in the
        # Discord Developer Portal — NOT a code bug. Fail loud and actionable
        # instead of dumping a 50-line asyncio traceback.
        sys.stderr.write(
            "\n"
            "============================================================\n"
            " DISCORD LOGIN FAILED — token rejected (HTTP 401).\n"
            " The bot code is fine; the token is invalid/revoked.\n"
            "\n"
            " Fix:\n"
            "  1. https://discord.com/developers/applications\n"
            "     → your app → Bot → 'Reset Token' → copy the new token.\n"
            "  2. Paste it into BOTH env files (they must match):\n"
            "       discord/.env   DISCORD_BOT_TOKEN=...\n"
            "       omerion/.env   DISCORD_BOT_TOKEN=...\n"
            "  3. Re-run this command.\n"
            "============================================================\n"
        )
        raise SystemExit(1)
