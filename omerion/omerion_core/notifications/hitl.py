"""HITL alert dispatch — posts to Discord via direct webhook.

The Discord webhook path swallows its own delivery errors; this facade
never raises because a notification miss must not block HITL review
creation (the review row in `founder_review_queue` is the durable record).
"""
from __future__ import annotations

from uuid import UUID

from omerion_core.logging import get_logger

log = get_logger("omerion.notifications.hitl")


def notify_hitl_review(
    *,
    review_id: UUID | str,
    agent_name: str,
    session_id: str,
    subject: str,
    context_md: str,
    approve_url: str,
    reject_url: str,
    correlation_id: UUID | str | None = None,
) -> str | None:
    """Post the HITL card to Discord. Returns the message id (for later in-place
    edit on resolve), or None if the webhook is unconfigured or delivery failed."""
    log.info("hitl_notify_dispatch", transport="discord_webhook", review_id=str(review_id))

    try:
        from omerion_core.notifications.discord_webhook import post_hitl_alert
        return post_hitl_alert(
            review_id=str(review_id),
            agent_name=agent_name,
            subject=subject,
            context_md=context_md,
            approve_url=approve_url,
            reject_url=reject_url,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("hitl_discord_webhook_exception", error=str(exc))
        return None
