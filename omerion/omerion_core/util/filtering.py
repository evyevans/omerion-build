"""Shared record-filtering primitives.

Why this lives in core: crm_nurture and linkedin_outreach each carried their
own copy of the stop-condition check, with subtly different field names. A
contact marked do_not_contact in CRM might still pass the LinkedIn check
because the two checks looked at slightly different schema. Centralizing the
rule keeps "stop sending to this person" from drifting between channels.
"""
from __future__ import annotations

from typing import Iterable

# The set of stop-condition keys an agent's config can opt into. Each key maps
# to a single boolean column on the contact record; if the config enables the
# key AND the column is true, the contact is filtered out.
STOP_CONDITION_KEYS = {
    "do_not_contact",
    "explicit_no",
    "signed_agreement",
    "meeting_booked",
    "replied",
}


def has_stop_condition(record: dict, enabled_conditions: Iterable[str]) -> bool:
    """Return True if any enabled stop-condition is set on the record."""
    enabled = set(enabled_conditions)
    for key in STOP_CONDITION_KEYS:
        if key in enabled and record.get(key):
            return True
    return False
