"""CRM Nurture — wrapper contract (Wave 1.9).

Email-based warm-lead nurture. Same archetype as `linkedin_outreach`:
the wrapper enforces opted-out filtering on the cohort, style-guard
filter on every draft body, and recipient verification on every
contact_id the agent touches.

Email recipients carry the same "AI cannot invent identities" guarantee
as LinkedIn: the wrapper rejects any contact_id in the output that
wasn't in the filtered input cohort. An LLM cannot construct an email
address that survives to the send queue.

The CRM nurture confidence floor matches linkedin_outreach (0.65) —
email is more recoverable than a LinkedIn DM (unsubscribe vs.
permanent connection-rejection) but a wrong recipient still wastes
opt-in trust.
"""
from __future__ import annotations

from typing import Literal

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)


class CrmNurtureInput(AgentInput):
    skill: Literal["crm-nurture"] = "crm-nurture"


class CrmNurtureOutput(AgentOutput):
    sent_count: int = 0
    failed_count: int = 0
    skipped_stop_condition: int = 0
    skipped_cooldown: int = 0
    rag_signals_written: int = 0


CONTRACT = AgentContract(
    skill="crm-nurture",
    input_model=CrmNurtureInput,
    output_model=CrmNurtureOutput,
    min_confidence=0.65,
    mutex_ttl_seconds=1800,
)


def _register() -> None:
    register_contract(CONTRACT)


_register()
