"""Lead Scraper & Enricher — wrapper contract + G2 people-data write gate.

The lead_scraper's job is to take an account_id and enrich real
decision-maker contacts from LinkedIn / Firecrawl / company sites via the
autonomous cognition loop (`cognition.enrich_account`). The risk: if upstream
(market_mapper or a fabricated input) produces a junk account, or the model
mis-extracts, junk contacts poison every downstream agent (icp_scoring,
outreach, offer_matching).

The defensive shape:

  1. The wrapper's contract sets `min_confidence=0.60` — relatively
     permissive because LLM extraction confidence is noisy by nature.
  2. Global HITL policy gate **G2** (`graph.hitl_gate_node`): the founder
     approves the whole enriched batch before ANY write to `contacts`.
     Upsert is a no-op unless approved (fail-closed).
  3. The wrapper's recipient verification still applies: every contact_id
     the agent enriches has to be one the agent itself just discovered
     — it cannot retroactively claim a contact for an unrelated account.
"""
from __future__ import annotations

from typing import Literal

from omerion_core.runtime.agent_wrapper import (
    AgentContract,
    AgentInput,
    AgentOutput,
    register_contract,
)


class LeadScraperInput(AgentInput):
    skill: Literal["lead-scraper"] = "lead-scraper"


class LeadScraperOutput(AgentOutput):
    contacts_enriched: int = 0
    batch_approved: bool = False        # founder's G2 decision on the enriched batch
    accounts_skipped: int = 0


CONTRACT = AgentContract(
    skill="lead-scraper",
    input_model=LeadScraperInput,
    output_model=LeadScraperOutput,
    min_confidence=0.60,
    mutex_ttl_seconds=1800,
)


def _register() -> None:
    register_contract(CONTRACT)


_register()
