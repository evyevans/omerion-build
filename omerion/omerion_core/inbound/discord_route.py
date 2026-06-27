"""Discord → Omerion routing endpoint.

The Omerion Discord bot forwards messages from channel-mapped Discord
channels to this endpoint so the correct agent skill can be triggered.

Flow:
    User types in #nurture
        → bot POSTs /inbound/discord/route (bearer auth)
        → Omerion maps channel_name → skill_name
        → Omerion triggers skill run (or returns a plaintext reply)
        → bot echoes the reply back into the Discord channel

Payload shape expected from the Discord bot:
    {
        "channel_name": "nurture",
        "guild_id": "1495488930697576612",
        "author": "evykynn",
        "message": "Score this lead: John Smith at Acme Solutions...",
        "session_id": "optional-existing-thread-id"
    }
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from omerion_core.inbound.signatures import require_bearer
from omerion_core.logging import get_logger

log = get_logger("omerion.inbound.discord_route")

router = APIRouter(
    prefix="/inbound/discord",
    tags=["discord"],
    dependencies=[Depends(require_bearer)],
)

# ── Channel → Skill routing table ─────────────────────────────────────────────
CHANNEL_SKILL_MAP: dict[str, str | None] = {
    "nurture": "crm-nurture",
    "scout": "lead-scraper",
    "score": "icp-scoring",
    "reach": "linkedin-outreach",
    "leads": "hq-lead-scraping",
    "match": "offer-matching",
    "oss":   "r2-oss-scout",
    "arch":  "r3-strategic-architect",
    "map": "market-mapper",
    "biz": "biz-dev-outreach",
    "newsletter": "newsletter_generator",
    "compliance": "compliance-checker",
    "security": "security-auditor",
    # 2026-06-21: #intel, #watch, #attrib, #onboard, #qa removed — their agents
    # (meeting-intelligence, r1-market-tech-watcher, outcome-attribution,
    # client-onboarding, qa-tester) migrated to the Claude Dev cloud platform.
    # Typing those channels now returns a clean 422 from the unknown-channel branch.
    # Wave 7.0 (Musk audit blocker #3): #eval, #success, #compete removed —
    # the three agents (eval-telemetry, client-success, competitive-intel)
    # were retired to deterministic scripts in the Wave 0 cutover. Their
    # routes were 500ing inside get_handler. If those Discord channels are
    # still being typed in, the user now gets a clean 422 from the unknown-
    # channel branch below.
    # read-only / aggregator channels — no skill triggered
    "founder-hitl": None,
    "mission-control": None,
}


class DiscordRouteRequest(BaseModel):
    channel_name: str = Field(..., description="Discord channel name (without #)")
    guild_id: str = Field(..., description="Discord Guild/Server ID")
    author: str = Field(..., description="Discord username of the sender")
    message: str = Field(..., description="Raw message text typed by the user")
    session_id: str | None = Field(
        default=None,
        description="Optional existing LangGraph thread ID to resume",
    )
    discord_channel_id: str | None = Field(
        default=None,
        description="Discord channel snowflake — needed for completion webhook callback",
    )
    discord_thread_id: str | None = Field(
        default=None,
        description="Discord thread snowflake when the message originated in a thread",
    )


class DiscordRouteResponse(BaseModel):
    skill: str | None
    routed: bool
    reply: str
    run_id: str | None = None
    session_id: str | None = None  # back-compat: equals run_id when a run was queued


READONLY_CHANNELS = {"mission-control", "founder-hitl"}

# Agents whose graph REQUIRES a domain identifier supplied only by an upstream
# event (a meeting transcript, a deployment, a completed build) — a free-text
# Discord prompt cannot produce that ID, so queuing a run would crash with a
# Pydantic ValidationError (intel/attrib) or fail downstream (qa). Instead of
# dispatching a doomed run, we reply with how the agent is actually triggered.
# (The graphs are NOT modified — this guard lives entirely at the route layer.)
# 2026-06-21: emptied — meeting-intel, outcome-attribution, and qa-tester all
# migrated to the Claude Dev cloud platform, so there are no event-only local
# agents left to guard. Kept as an empty dict so the lookup below still works.
DISCORD_EVENT_ONLY: dict[str, str] = {}


@router.post("/route", response_model=DiscordRouteResponse)
def discord_route(
    body: DiscordRouteRequest,
    background_tasks: BackgroundTasks,
) -> DiscordRouteResponse:
    """Receive a Discord message, map channel→skill, queue an agent run."""
    channel = body.channel_name.lstrip("#").lower()
    skill = CHANNEL_SKILL_MAP.get(channel)

    log.info(
        "discord_message_received",
        channel=channel,
        author=body.author,
        skill=skill,
        session_id=body.session_id,
    )

    # Read-only / aggregator channels — acknowledge without dispatching.
    # founder-hitl maps to None and must NEVER trigger an agent run; it
    # exists only for Discord HITL approve/reject buttons.
    if channel in READONLY_CHANNELS:
        return DiscordRouteResponse(
            skill=None,
            routed=False,
            reply=f"#{channel} is read-only. No agents are triggered from this channel.",
        )

    # Unknown channel, or a channel explicitly mapped to None that wasn't
    # already short-circuited by READONLY_CHANNELS above.
    # Wave 7.0 (Musk audit blocker #3): the previous wildcard fallback
    # silently coerced None-mapped channels to build-orchestrator, which
    # masked misconfiguration. Now any unmapped/None route returns 422.
    if skill is None:
        log.warning("discord_unmapped_channel", channel=channel, in_map=channel in CHANNEL_SKILL_MAP)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No skill mapped to channel #{channel}. Check CHANNEL_SKILL_MAP.",
        )

    # Event-only agents: a typed prompt can't supply the domain ID their graph
    # requires. Reply with how the agent is actually triggered instead of queuing
    # a run that would ValidationError/fail. (Graph-safe: no run is dispatched.)
    if skill in DISCORD_EVENT_ONLY:
        log.info("discord_event_only_channel", channel=channel, skill=skill)
        return DiscordRouteResponse(skill=skill, routed=False, reply=DISCORD_EVENT_ONLY[skill])

    target_skill = skill

    # Queue the run via the same lifecycle path the control plane uses.
    try:
        from omerion_core.runtime import run_lifecycle
        from omerion_core.runtime.registry import get_handler
        from omerion_core.runtime.run_executor import execute_run
    except ImportError as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"runtime unavailable: {exc}") from exc

    try:
        get_handler(target_skill)
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"channel #{channel} maps to skill '{target_skill}' but no handler is registered",
        ) from exc

    run = run_lifecycle.create_run(
        agent_name=target_skill,
        source_channel="discord",
        inputs={
            "discord_message": body.message,
            "author": body.author,
            "guild_id": body.guild_id,
            "channel_name": channel,
        },
        triggered_by=body.author,
        discord_channel_id=body.discord_channel_id,
        discord_thread_id=body.discord_thread_id,
    )
    background_tasks.add_task(execute_run, run["run_id"])

    short = run["run_id"][:8]
    reply = (
        f"✅ Queued **{target_skill}** — run `{short}`.\n"
        f"💬 _\"{body.message}\"_\n\n"
        f"I'll post results in this channel as soon as it's done."
    )

    log.info(
        "discord_run_queued",
        channel=channel,
        skill=target_skill,
        run_id=run["run_id"],
        author=body.author,
    )

    return DiscordRouteResponse(
        skill=target_skill,
        routed=True,
        reply=reply,
        run_id=run["run_id"],
        session_id=run["run_id"],
    )
