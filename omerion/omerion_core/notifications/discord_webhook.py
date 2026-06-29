"""Direct Discord webhook transport — completion + HITL notifications.

Discord webhooks are bound to a specific channel; to target a thread
within that channel, append `?thread_id=<snowflake>` to the URL — the
executor passes that through from the originating Discord message.

Failures NEVER raise: a delivery problem must not corrupt the run lifecycle.
"""
from __future__ import annotations

from typing import Any

import httpx

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.notifications.discord_webhook")

_TIMEOUT = 5.0
_MAX_CONTENT_LEN = 1900  # Discord limit is 2000; leave headroom for formatting

_STATUS_EMOJI = {
    "completed": "✅",
    "failed": "❌",
    "cancelled": "🚫",
    "hitl_waiting": "⏸️",
}
_STATUS_COLOR = {
    "completed": 0x2ECC71,
    "failed": 0xE74C3C,
    "cancelled": 0x95A5A6,
    "hitl_waiting": 0xF1C40F,
}


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _post(url: str, payload: dict[str, Any], thread_id: str | None) -> bool:
    target = url
    if thread_id:
        sep = "&" if "?" in target else "?"
        target = f"{target}{sep}thread_id={thread_id}"
    try:
        resp = httpx.post(target, json=payload, timeout=_TIMEOUT)
        if resp.status_code >= 300:
            log.warning(
                "discord_webhook_non_2xx",
                status=resp.status_code,
                body=_truncate(resp.text, 200),
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("discord_webhook_post_failed", error=str(exc))
        return False


def _post_returning_id(url: str, payload: dict[str, Any]) -> str | None:
    """POST to a webhook and return the created message id, or None on failure.

    Discord webhooks return ``204 No Content`` by default and only echo the
    created message object (including its ``id``) when ``?wait=true`` is set.
    We need the id to edit the card in place later (e.g. strip approve/reject
    links once the founder decides), so this path always waits.
    """
    sep = "&" if "?" in url else "?"
    target = f"{url}{sep}wait=true"
    try:
        resp = httpx.post(target, json=payload, timeout=_TIMEOUT)
        if resp.status_code >= 300:
            log.warning(
                "discord_webhook_non_2xx",
                status=resp.status_code,
                body=_truncate(resp.text, 200),
            )
            return None
        message_id = resp.json().get("id")
        return str(message_id) if message_id else None
    except Exception as exc:  # noqa: BLE001
        log.warning("discord_webhook_post_failed", error=str(exc))
        return None


def _edit_webhook_message(url: str, message_id: str, payload: dict[str, Any]) -> bool:
    """Edit a previously-posted webhook message via PATCH.

    The webhook token already embedded in ``url`` authorizes editing messages
    that this webhook created — no bot token required. Endpoint shape:
    ``PATCH /webhooks/{id}/{token}/messages/{message_id}``.
    """
    if not url or not message_id:
        return False
    base = url.split("?", 1)[0].rstrip("/")  # webhook URLs carry no query string
    target = f"{base}/messages/{message_id}"
    try:
        resp = httpx.patch(target, json=payload, timeout=_TIMEOUT)
        if resp.status_code >= 300:
            log.warning(
                "discord_webhook_edit_non_2xx",
                message_id=message_id,
                status=resp.status_code,
                body=_truncate(resp.text, 200),
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("discord_webhook_edit_failed", message_id=message_id, error=str(exc))
        return False


_DISCORD_API_BASE = "https://discord.com/api/v10"


def _post_to_channel(channel_id: str, content: str, *, bot_token: str) -> bool:
    """Post a plain-text message to a Discord channel via the Bot API.

    Uses the Bot token auth scheme (``Authorization: Bot <token>``), which
    is distinct from the webhook URL scheme used by ``_post()``.
    """
    if not channel_id or not bot_token:
        return False
    try:
        resp = httpx.post(
            f"{_DISCORD_API_BASE}/channels/{channel_id}/messages",
            headers={
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json",
            },
            json={"content": _truncate(content, _MAX_CONTENT_LEN)},
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 300:
            log.warning(
                "discord_channel_post_non_2xx",
                channel_id=channel_id,
                status=resp.status_code,
                body=_truncate(resp.text, 200),
            )
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("discord_channel_post_failed", channel_id=channel_id, error=str(exc))
        return False


def _build_run_embed(run: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return (embed_dict, fields_list) from a run row — shared by both post paths."""
    status_str = run.get("status") or "completed"
    agent = run.get("agent_name") or "agent"
    run_id = run.get("run_id") or ""
    short = run_id[:8] if run_id else ""
    emoji = _STATUS_EMOJI.get(status_str, "ℹ️")
    color = _STATUS_COLOR.get(status_str, 0x3498DB)

    title = f"{emoji} {agent} — {status_str}"
    if short:
        title += f"  ({short})"

    description_parts: list[str] = []
    if status_str == "failed" and run.get("error"):
        description_parts.append(f"**Error:** {_truncate(str(run['error']), 500)}")
    elif run.get("result_summary"):
        description_parts.append(_truncate(str(run["result_summary"]), 1500))

    fields: list[dict[str, Any]] = []
    if run.get("cost_usd") is not None:
        fields.append({"name": "Cost", "value": f"${run['cost_usd']}", "inline": True})
    if run.get("triggered_by"):
        fields.append({"name": "Triggered by", "value": str(run["triggered_by"]), "inline": True})

    embed: dict[str, Any] = {
        "title": _truncate(title, 250),
        "description": _truncate("\n\n".join(description_parts), _MAX_CONTENT_LEN),
        "color": color,
    }
    if fields:
        embed["fields"] = fields

    return embed, fields


def _build_channel_summary(run: dict[str, Any]) -> str:
    """Build a conversational summary string for the originating Discord channel."""
    status_str = run.get("status") or "completed"
    agent = run.get("agent_name") or "agent"
    run_id = run.get("run_id") or ""
    short = run_id[:8] if run_id else ""
    emoji = _STATUS_EMOJI.get(status_str, "ℹ️")

    lines = [f"{emoji} **{agent}** finished — run `{short}` — **{status_str}**"]
    if status_str == "failed" and run.get("error"):
        lines.append(f"> {_truncate(str(run['error']), 300)}")
    elif run.get("result_summary"):
        lines.append(_truncate(str(run["result_summary"]), 800))
    if run.get("cost_usd") is not None:
        lines.append(f"💰 Cost: ${run['cost_usd']}")
    return "\n".join(lines)


def post_run_completion(run: dict[str, Any]) -> bool:
    """Post the terminal state of an agent run to the correct Discord destination(s).

    Always posts to the global #mission-control completion webhook (structured
    embed, backend logging).  When the run originated from a Discord channel
    (``discord_channel_id`` is set), additionally posts a conversational
    summary back into that originating channel using the Bot API so the user
    sees results where they typed the command.

    Returns True if the mission-control webhook post succeeded.
    """
    # ── 1. Global mission-control embed (unchanged behaviour) ─────────────────
    mc_ok = False
    url = settings.discord_completion_webhook_url
    if url:
        embed, _ = _build_run_embed(run)
        payload = {"embeds": [embed]}
        mc_ok = _post(url, payload, thread_id=run.get("discord_thread_id"))

    # ── 2. Originating-channel reply via Bot API ───────────────────────────────
    channel_id = run.get("discord_channel_id")
    bot_token = settings.discord_bot_token
    if channel_id and bot_token:
        summary = _build_channel_summary(run)
        _post_to_channel(channel_id, summary, bot_token=bot_token)

    return mc_ok


def post_hitl_alert(
    *,
    review_id: str,
    agent_name: str,
    subject: str,
    context_md: str,
    approve_url: str,
    reject_url: str,
) -> str | None:
    """Post a HITL approval card to the HITL webhook.

    Returns the Discord message id (so the card can be edited in place when the
    review is resolved), or None if the webhook is unconfigured or delivery failed.
    """
    url = settings.discord_hitl_webhook_url
    if not url:
        return None

    description_parts = [_truncate(context_md, 1500)]
    if approve_url:
        description_parts.append(f"[✅ Approve]({approve_url})")
    if reject_url:
        description_parts.append(f"[❌ Reject]({reject_url})")

    embed = {
        "title": _truncate(f"⏸️ HITL — {agent_name}: {subject}", 250),
        "description": _truncate("\n\n".join(description_parts), _MAX_CONTENT_LEN),
        "color": _STATUS_COLOR["hitl_waiting"],
        "footer": {"text": f"review_id: {review_id}"},
    }
    return _post_returning_id(url, {"embeds": [embed]})


def edit_hitl_resolved(
    *,
    message_id: str,
    review_id: str,
    agent_name: str,
    subject: str,
    decision: str,        # 'approved' | 'rejected'
    decided_at: str | None = None,
) -> bool:
    """Rewrite a posted HITL card to its resolved state — no approve/reject links.

    Called after the founder decides, so the card stops showing live action links
    and instead stamps who decided and when. Best-effort: returns False (never
    raises) if the webhook is unconfigured or the edit fails.
    """
    url = settings.discord_hitl_webhook_url
    if not url or not message_id:
        return False

    approved = decision == "approved"
    icon = "✅" if approved else "❌"
    verb = "Approved" if approved else "Rejected"
    color = _STATUS_COLOR["completed"] if approved else _STATUS_COLOR["failed"]

    when = ""
    if decided_at:
        # decided_at is an ISO timestamp; show a compact UTC stamp.
        when = f" · {decided_at[:16].replace('T', ' ')} UTC"

    embed = {
        "title": _truncate(f"{icon} {verb} — {agent_name}: {subject}", 250),
        "description": f"{icon} **{verb} by founder**{when}",
        "color": color,
        "footer": {"text": f"review_id: {review_id}"},
    }
    return _edit_webhook_message(url, message_id, {"embeds": [embed]})
