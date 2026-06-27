"""
Alert handler — utility functions for sending structured Discord alerts.
Used by tools that need to surface errors or flags without a full embed.
"""
import os
import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger("alert_handler")

_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
_ALERT_WEBHOOK_URL = os.environ.get("DISCORD_ALERT_WEBHOOK_URL", os.environ.get("DISCORD_WEBHOOK_URL", ""))


def _post(webhook_url: str, payload: dict) -> None:
    if not webhook_url:
        log.warning("No Discord webhook URL configured — alert not sent: %s", payload)
        return
    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()


def send_alert(title: str, message: str, level: str = "warning") -> None:
    """
    level: "info" | "warning" | "error"
    """
    color_map = {"info": 0x5865F2, "warning": 0xFAA61A, "error": 0xED4245}
    emoji_map = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}

    payload = {
        "embeds": [{
            "title": f"{emoji_map.get(level, '⚠️')} {title}",
            "description": message,
            "color": color_map.get(level, 0xFAA61A),
            "footer": {"text": "Omerion AI • Alert"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    _post(_ALERT_WEBHOOK_URL, payload)


def send_scout_failure(job_title: str, contact_id: str, reason: str) -> None:
    send_alert(
        title="Scout Classification Failure",
        message=(
            f"**Contact:** {contact_id}\n"
            f"**Job Title:** `{job_title}`\n"
            f"**Reason:** {reason}\n"
            f"Manual persona assignment required."
        ),
        level="warning",
    )


def send_webhook_unknown_sender(email: str) -> None:
    send_alert(
        title="Unknown Email Sender",
        message=f"Inbound reply from **{email}** could not be matched to a contact. Manual review task created.",
        level="info",
    )


def send_deployment_error(skill_name: str, error: str) -> None:
    send_alert(
        title=f"Deployment Error — {skill_name}",
        message=f"```\n{error[:1000]}\n```",
        level="error",
    )
