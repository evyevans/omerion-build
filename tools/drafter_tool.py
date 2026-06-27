"""
Drafter tool — personalizes a template (fetched from BackboneEngine) using Claude,
then hands the draft back to BackboneEngine to write into the Review Queue.

Does NOT: select templates, count sequence steps, check the 4-touch cap.
Does: personalize copy, write to Review Queue via BackboneEngine.
"""
import os
import logging
import requests

import anthropic

from tools.deployment_logger import log_deployment

log = logging.getLogger("drafter_tool")

_BACKBONE_URL = os.environ.get("BACKBONE_ENDPOINT_URL", "")
_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

_client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)


def _get_template(contact_id: str, channel: str) -> dict:
    """Ask BackboneEngine to select the correct template for this contact."""
    resp = requests.get(_BACKBONE_URL, params={
        "action": "selectTemplate",
        "contact_id": contact_id,
        "channel": channel,
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _get_sequence_position(contact_id: str) -> dict:
    """Ask BackboneEngine for current sequence position."""
    resp = requests.get(_BACKBONE_URL, params={
        "action": "getSequencePosition",
        "contact_id": contact_id,
    }, timeout=15)
    resp.raise_for_status()
    return resp.json()


@log_deployment(skill_name="drafter_personalize", triggered_by="pipeline", model="claude-sonnet-4-6")
def _personalize(subject: str, body: str, contact: dict) -> dict:
    prompt = (
        f"You are personalizing a cold outreach email for a real estate AI consultant.\n"
        f"Insert the contact's first name and a brief reference to their role/company where natural.\n"
        f"Do NOT change the CTA, tone, or structure.\n\n"
        f"Contact: {contact.get('first_name')} {contact.get('last_name')}, "
        f"{contact.get('persona')} at {contact.get('company_name', 'their company')}\n\n"
        f"Subject template:\n{subject}\n\n"
        f"Body template:\n{body}\n\n"
        f"Return JSON: {{\"subject\": \"...\", \"body\": \"...\"}}"
    )
    response = _client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    _personalize._last_usage = response.usage
    import json
    text = response.content[0].text.strip()
    return json.loads(text)


def draft_for_contact(contact: dict, channel: str = "email") -> None:
    contact_id = contact["id"]

    position = _get_sequence_position(contact_id)
    if position.get("capped"):
        log.info("Contact %s is capped at 4 touches — skipping.", contact_id)
        return

    template = _get_template(contact_id, channel)
    personalized = _personalize(template["subject"], template["body"], contact)

    resp = requests.post(_BACKBONE_URL, json={
        "action": "createReviewQueueItem",
        "data": {
            "contact_id": contact_id,
            "channel": channel,
            "subject": personalized["subject"],
            "body": personalized["body"],
            "status": "Pending Approval",
        },
    }, timeout=15)
    resp.raise_for_status()
    log.info("Draft queued for contact %s (step %s)", contact_id, position.get("position"))
