"""LangGraph for Lead Scraper & Enricher (Agent #3).

LangGraph owns the durable edges; the model owns enrichment cognition.

Flow:
    parse_discord_intent → load_accounts → cognition → hitl_gate(G2) → upsert → emit → END

- `parse_discord_intent` is a no-op when account_ids are already populated
  (event-triggered runs from market-mapper's ACCOUNT_BATCH_READY, or cron). It only
  fires for Discord-triggered runs with a free-text message and no UUIDs, and may
  delegate to market-mapper for market_search intents.
- `cognition` runs one autonomous enrichment loop per account (cognition.py).
- `hitl_gate` is the global-policy G2 gate: the founder approves the whole batch of
  contacts before any write to `contacts`. Upsert is a no-op unless approved.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.exceptions import UserFacingError
from omerion_core.hitl.policy import Gate, ReviewItem, gate
from omerion_core.llm.router import ClaudeRouter
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .cognition import enrich_account
from .state import EnricherState
from .tools import (
    create_placeholder_account,
    extract_companies_from_message,
    index_contact,
    load_accounts,
    upsert_contact,
)

log = get_logger("omerion.agents.lead_scraper_enricher")


@traced_node("parse_discord_intent")
def parse_discord_intent_node(state: EnricherState) -> EnricherState:
    """Convert a free-text Discord message into account_ids when none are provided.

    Short-circuits when account_ids are already populated (event/cron runs). For
    Discord runs the caller provides `discord_message`; this node extracts company
    names/domains and upserts placeholder accounts. A market-description message is
    delegated to the Market Mapper, whose discovered accounts are then enriched.
    """
    if state.account_ids:
        return state  # already have IDs — nothing to do

    msg = state.discord_message or state.scratch.get("discord_message") or ""
    if not msg:
        log.warning("lead_scraper_no_input", hint="Provide account_ids or discord_message")
        return state

    router = ClaudeRouter()
    result = extract_companies_from_message(router, msg)
    intent = result["intent"]
    companies = result["companies"]
    log.info("parse_discord_intent_extracted", intent=intent, count=len(companies),
             companies=[c.get("name") for c in companies])

    if intent == "market_search" or not companies:
        # Delegate to the Market Mapper to discover companies matching the
        # description, then continue enriching them (a real multi-agent handoff).
        log.info("lead_scraper_delegating_to_market_mapper", message=msg[:100])
        try:
            from uuid import UUID as _UUID

            from omerion_core.runtime.registry import run_agent_by_name

            mm_result = run_agent_by_name("market-mapper", {
                "target_markets": [msg],
                "session_id": state.session_id or "",
                "correlation_id": state.correlation_id,
                "agent_name": "market_mapper",
            })

            mm_final_state = mm_result.get("result") or {}
            discovered = []
            for candidate in (mm_final_state.get("candidates") or []):
                aid = candidate.get("account_id") if isinstance(candidate, dict) else getattr(candidate, "account_id", None)
                if aid is not None:
                    try:
                        discovered.append(_UUID(str(aid)))
                    except (ValueError, AttributeError):
                        pass

            if not discovered:
                raise UserFacingError(
                    "I ran Market Mapper on your description but found no qualifying companies. "
                    "Try a more specific market description (e.g. 'B2B SaaS companies in Toronto with 10-50 employees')."
                )

            state.account_ids = discovered
            log.info("lead_scraper_market_mapper_chain_complete", discovered=len(discovered))
        except UserFacingError:
            raise
        except Exception as exc:
            log.error("lead_scraper_market_mapper_chain_failed", error=str(exc))
            raise UserFacingError(
                "Market Mapper failed while trying to find companies matching your description. "
                f"Reason: {exc}"
            ) from exc

    ids = []
    for c in companies:
        account_id = create_placeholder_account(c.get("name", ""), c.get("domain", ""))
        if account_id is not None:
            ids.append(account_id)

    state.account_ids = ids
    log.info("parse_discord_intent_accounts_created", count=len(ids))
    return state


@traced_node("load_accounts")
def load_accounts_node(state: EnricherState) -> EnricherState:
    accounts = load_accounts(state.account_ids)
    state.scratch["accounts"] = {str(a["account_id"]): a for a in accounts}
    return state


@traced_node("cognition")
def cognition_node(state: EnricherState) -> EnricherState:
    """Autonomous enrichment loop — one self-correcting pass per account.

    The model reads the LinkedIn page + company site, filters real people from
    noise, verifies emails via Hunter when worthwhile, and assigns persona. Python
    here only fans out over the accounts and accumulates cost.
    """
    accounts = state.scratch.get("accounts", {})
    if not accounts:
        return state
    router = ClaudeRouter()
    cfg = settings.agent("lead_scraper_enricher")
    max_contacts = int(cfg.get("max_contacts_per_account", 3))

    for account in accounts.values():
        try:
            contacts, cost = enrich_account(
                router, account, max_contacts=max_contacts,
                run_id=state.session_id, correlation_id=state.correlation_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("enricher_cognition_failed", account_id=account.get("account_id"), error=str(exc))
            continue
        state.enrichment_cost_usd += cost
        state.enriched.extend(contacts)

    log.info("enricher_cognition_done", contacts=len(state.enriched),
             cost_usd=round(state.enrichment_cost_usd, 4))
    return state


@traced_node("hitl_gate")
def hitl_gate_node(state: EnricherState) -> EnricherState:
    """G2 — people-data write gate. Founder approves the whole batch before upsert.

    One review card summarizes every contact about to be written to `contacts`.
    Routed through the global HITL policy; fail-closed (no approval → no write).
    """
    if not state.enriched:
        return state

    accounts = state.scratch.get("accounts", {})
    lines = []
    for c in state.enriched:
        acct = accounts.get(str(c.account_id), {})
        lines.append(
            f"- **{c.full_name}** — {c.title or '—'} · `{c.persona}` · "
            f"{c.email or 'no email'}  _({acct.get('name', c.account_id)})_"
        )
    context_md = (
        f"**{len(state.enriched)} contact(s)** enriched and ready to write to `contacts`.\n\n"
        + "\n".join(lines)
        + "\n\nApprove to upsert all. Reject to discard the batch."
    )
    item = ReviewItem(
        key=state.session_id or "batch",
        subject=f"Approve {len(state.enriched)} enriched contact(s)?",
        context_md=context_md,
        draft_ref={"kind": "contact_batch", "count": len(state.enriched)},
    )

    decisions = gate(
        Gate.EXTERNAL_PEOPLE_DATA_WRITE,
        [item],
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        correlation_id=state.correlation_id,
    )
    state.batch_approved = decisions.get(item.key) == "approved"
    log.info("enricher_hitl_decision", approved=state.batch_approved, count=len(state.enriched))
    return state


@traced_node("upsert")
def upsert_node(state: EnricherState) -> EnricherState:
    if not state.batch_approved:
        log.info("enricher_upsert_skipped_unapproved", count=len(state.enriched))
        return state
    for contact in state.enriched:
        if contact.email is None and contact.full_name == "Unknown":
            state.duplicates_skipped += 1
            continue
        contact_id = upsert_contact(contact)
        if contact_id is None:
            state.duplicates_skipped += 1
            continue
        contact.contact_id = contact_id
        state.upserted += 1
    return state


@traced_node("index_contacts")
def index_contacts_node(state: EnricherState) -> EnricherState:
    if not state.batch_approved:
        return state
    # state.scratch["accounts"] is a dict {str(account_id): account_row} set by load_accounts_node.
    accounts_by_id = {aid: a.get("name", "") for aid, a in state.scratch.get("accounts", {}).items()}
    indexed = 0
    for c in state.enriched:
        if c.contact_id is None:
            continue
        account_name = accounts_by_id.get(str(c.account_id), "")
        vid = index_contact(c, account_name=account_name, run_date=str(state.run_date))
        if vid:
            indexed += 1
    log.info("lead_enricher_indexed", count=indexed)
    return state


@traced_node("emit")
def emit_node(state: EnricherState) -> EnricherState:
    for contact in state.enriched:
        if contact.contact_id is None:
            continue
        emit_event(
            EventType.CONTACT_ENRICHED,
            source_agent=state.agent_name,
            payload={
                "contact_id": str(contact.contact_id),
                "account_id": str(contact.account_id),
                "persona": contact.persona,
            },
            correlation_id=state.correlation_id,
        )
        state.emitted_events += 1
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(EnricherState)
    g.add_node("parse_discord_intent", parse_discord_intent_node)
    g.add_node("load_accounts", load_accounts_node)
    g.add_node("cognition", cognition_node)
    g.add_node("hitl_gate", hitl_gate_node)
    g.add_node("upsert", upsert_node)
    g.add_node("index_contacts", index_contacts_node)
    g.add_node("emit", emit_node)
    g.set_entry_point("parse_discord_intent")
    g.add_edge("parse_discord_intent", "load_accounts")
    g.add_edge("load_accounts", "cognition")
    g.add_edge("cognition", "hitl_gate")
    g.add_edge("hitl_gate", "upsert")
    g.add_edge("upsert", "index_contacts")
    g.add_edge("index_contacts", "emit")
    g.add_edge("emit", END)
    return g.compile(checkpointer=get_checkpointer())
