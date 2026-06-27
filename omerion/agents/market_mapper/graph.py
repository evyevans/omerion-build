"""LangGraph for Market Mapper (Agent #1).

Flow:
    seed_markets
      → scrape           (per market: fetch raw candidate accounts)
      → classify         (Claude Haiku — persona segment per account)
      → rank             (deterministic volume + fit + tech_maturity)
      → upsert           (markets row + accounts upsert idempotent on (domain, market_id))
      → emit             (one ACCOUNT_BATCH_READY per market)
"""
from __future__ import annotations

from collections import defaultdict

from langgraph.graph import END, StateGraph

from omerion_core.events.bus import EventType, emit_event
from omerion_core.llm.router import claude
from omerion_core.logging import get_logger
from omerion_core.telemetry.middleware import traced_node

from .state import MarketMapState
from .tools import (
    classify_persona,
    emit_batch_ready_payload,
    rank,
    scrape_market,
    target_markets,
    upsert_account,
    upsert_market,
)

log = get_logger("omerion.agents.market_mapper")


@traced_node("seed_markets")
def seed_node(state: MarketMapState) -> MarketMapState:
    if not state.target_markets:
        state.target_markets = target_markets()
    log.info("market_mapper_seeded", markets=state.target_markets)
    return state


@traced_node("scrape")
def scrape_node(state: MarketMapState) -> MarketMapState:
    for m in state.target_markets:
        state.candidates.extend(scrape_market(m))
    log.info("market_mapper_scraped", count=len(state.candidates))
    return state


@traced_node("classify")
def classify_node(state: MarketMapState) -> MarketMapState:
    if not state.candidates:
        return state
    router = claude()
    for a in state.candidates:
        a.persona = classify_persona(router, a)
    return state


@traced_node("rank")
def rank_node(state: MarketMapState) -> MarketMapState:
    for a in state.candidates:
        rank(a, a.persona)
    return state


@traced_node("upsert")
def upsert_node(state: MarketMapState) -> MarketMapState:
    market_ids: dict[str, str] = {}
    for m in state.target_markets:
        mid = upsert_market(m)
        if mid is not None:
            market_ids[m] = str(mid)
    for a in state.candidates:
        if not a.qualifies:
            state.accounts_skipped_threshold += 1
            continue
        mid = market_ids.get(a.market)
        from uuid import UUID as _U
        a.account_id = upsert_account(a, _U(mid) if mid else None, market_name=a.market)
        if a.account_id is not None:
            state.accounts_upserted += 1
    return state


@traced_node("emit")
def emit_node(state: MarketMapState) -> MarketMapState:
    by_market: dict[str, list] = defaultdict(list)
    for a in state.candidates:
        if a.account_id is not None and a.qualifies:
            by_market[a.market].append(a)
    import uuid as _uuid
    for market, accounts in by_market.items():
        payload = emit_batch_ready_payload(accounts)
        payload["market"] = market
        payload.setdefault("batch_id", str(_uuid.uuid4()))
        payload.setdefault("account_ids", [str(a.account_id) for a in accounts if a.account_id])
        emit_event(
            EventType.ACCOUNT_BATCH_READY,
            source_agent=state.agent_name,
            payload=payload,
            correlation_id=state.correlation_id,
        )
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(MarketMapState)
    g.add_node("seed_markets", seed_node)
    g.add_node("scrape", scrape_node)
    g.add_node("classify", classify_node)
    g.add_node("rank", rank_node)
    g.add_node("upsert", upsert_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("seed_markets")
    g.add_edge("seed_markets", "scrape")
    g.add_edge("scrape", "classify")
    g.add_edge("classify", "rank")
    g.add_edge("rank", "upsert")
    g.add_edge("upsert", "emit")
    g.add_edge("emit", END)
    return g.compile(checkpointer=get_checkpointer())
