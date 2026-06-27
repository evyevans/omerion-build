"""LangGraph for High-Quality Lead Scraping (Agent #2).

LangGraph owns only the durable edges (trigger → cognition → HITL interrupt →
persist → emit). The cognition itself is one autonomous tool-use loop
(`cognition.research_account`) — no rigid research/synthesize/quality routing.

Flow:
    parse_discord_intent  (only when triggered from Discord; cron skips it)
      → load_priority_accounts
      → cognition               (autonomous loop per account → vetted Dossier + semantic dedup)
      → hitl_gate               (G2 people-data write — founder approves each dossier)
      → persist_and_index       (research_dossiers + Pinecone upsert)
      → emit
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from omerion_core.clients.supabase_client import supabase
from omerion_core.events.bus import EventType, emit_event
from omerion_core.exceptions import UserFacingError
from omerion_core.hitl.policy import Gate, ReviewItem, gate
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings
from omerion_core.telemetry.middleware import traced_node

from .cognition import dedup_status, research_account
from .prompts import REVIEW_HEADER
from .state import HQLState
from .tools import (
    index_dossier,
    load_priority_accounts,
    upsert_research_account,
    write_dossier,
)

log = get_logger("omerion.agents.high_quality_lead_scraping")

# ── Discord intent parsing ────────────────────────────────────────────────────
_INTENT_SYSTEM = (
    "You are a JSON extractor. The founder just sent a lead-gen request. "
    "Extract: target_persona (string), target_industry (string), "
    "example_companies (list of company names or domains, up to 5). "
    "Return ONLY valid JSON with those three keys."
)


@traced_node("parse_discord_intent")
def parse_discord_intent_node(state: HQLState) -> HQLState:
    """Parse a natural-language Discord request into search criteria.

    If the run was triggered from Discord (discord_message is set), we use
    Claude to extract target persona / industry / example companies, then
    create stub account rows so the rest of the graph has something to research.
    Cron-triggered runs skip this node entirely.
    """
    # state.discord_message is populated by event_ingress for Discord-triggered runs.
    # (Was `(state.inputs or {})` — AgentRunState has no `inputs` field, so that
    # raised AttributeError and crashed the reactive #leads path.)
    msg = state.discord_message or state.scratch.get("discord_message", "") or ""
    if not msg:
        state.mode = "proactive"  # cron/scheduler-seeded — no Discord intent to parse
        return state

    state.discord_message = msg
    router = ClaudeRouter()
    hint: dict = {}
    try:
        from omerion_core.llm.json_extraction import extract_json_object
        resp = router.complete(
            system=_INTENT_SYSTEM,
            prompt=msg,
            tier=Tier.FAST,
            max_tokens=300,
            temperature=0.1,
        )
        hint, ok = extract_json_object(resp["text"])
        if ok:
            state.search_hint = hint
    except Exception as exc:  # noqa: BLE001
        log.warning("discord_intent_parse_failed", error=str(exc))

    example_companies: list[str] = hint.get("example_companies") or []

    # Fail-loud when Discord-triggered AND the LLM extracted no companies AND
    # the caller didn't provide an explicit candidate_account_ids override.
    # Otherwise Opus would burn budget on $0-yield runs (see SCOUT Failure 9).
    if not example_companies and not state.candidate_account_ids:
        raise UserFacingError(
            "No specific companies named in your message. SOURCE deep-researches "
            "named companies one at a time — give me 1–5 explicit company names "
            "or domains (e.g. 'research Acme Corp and stripe.com'). To discover "
            "companies by industry/persona criteria first, use Market Mapper."
        )

    # Upsert each example company into the accounts table so we get a real,
    # DB-resident account_id. Without this, write_dossier's INSERT into
    # research_dossiers would FK-fail on a fake uuid4() — AFTER Opus billed.
    for company in example_companies[:5]:
        domain = company.lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
        account_id = upsert_research_account(name=company, domain=domain)
        if account_id is None:
            continue
        state.accounts.append({
            "account_id": str(account_id),
            "name": company,
            "domain": domain,
            "market_id": None,
            "tier": "tier_1",
            "team_size_bucket": None,
            "persona": hint.get("target_persona", "b2b_saas"),
            "metadata": {"source": "discord_request"},
        })

    log.info(
        "discord_intent_parsed",
        message_preview=msg[:80],
        stub_accounts=len(state.accounts),
        hint=hint,
    )
    return state


@traced_node("load_priority_accounts")
def load_node(state: HQLState) -> HQLState:
    existing_ids = {a["account_id"] for a in state.accounts if a.get("account_id")}
    db_accounts = load_priority_accounts(state.candidate_account_ids or None)
    merged = list(state.accounts)
    for a in db_accounts:
        if a.get("account_id") not in existing_ids:
            merged.append(a)

    # Enforce max_accounts_per_cycle on the MERGED list, not just db_accounts.
    # Without this re-cap, a Discord run with 5 stubs + a full priority queue
    # could process 20+ accounts and blow the Opus cost budget.
    cfg = settings.agent("high_quality_lead_scraping")
    cap = int(cfg.get("max_accounts_per_cycle", 15))
    if len(merged) > cap:
        log.warning("hql_accounts_capped", merged=len(merged), cap=cap)
        merged = merged[:cap]

    state.accounts = merged
    log.info("hql_accounts_loaded", count=len(state.accounts))
    return state


@traced_node("cognition")
def cognition_node(state: HQLState) -> HQLState:
    """Autonomous research loop — one self-correcting agent pass per account.

    Replaces the old research → synthesize → quality_gate trio. The model decides
    what to fetch, reasons about quality itself, and finalizes a Dossier. Python
    fans out over the worklist only; semantic dedup is handled by the next node
    so Pinecone availability cannot silently swallow dossiers here.
    """
    if not state.accounts:
        return state
    router = ClaudeRouter()
    for a in state.accounts:
        try:
            dossier, cost = research_account(
                router, a, run_id=state.session_id, correlation_id=state.correlation_id
            )
        except Exception as exc:  # noqa: BLE001
            log.error("hql_cognition_failed", account_id=a.get("account_id"), error=str(exc),
                      exc_info=True)
            continue
        state.research_cost_usd += cost

        # No finalized dossier (turn budget exhausted) or degenerate output → skip.
        if dossier is None or not dossier.summary:
            state.skipped_low_quality += 1
            continue

        state.dossiers.append(dossier)

    log.info("hql_cognition_done", dossiers=len(state.dossiers),
             skipped_low_quality=state.skipped_low_quality,
             cost_usd=round(state.research_cost_usd, 4))
    return state


@traced_node("dedup_filter")
def dedup_filter_node(state: HQLState) -> HQLState:
    """Semantic dedup against existing Pinecone dossiers — isolated from cognition.

    Hard skip (≥0.96) → removed; counted in state.skipped_duplicate.
    Soft flag (0.90–0.95) → dedup_note added for founder visibility in HITL card.
    Pinecone outage → fail-open: all dossiers pass through to the HITL gate so
    the founder can manually decide rather than losing work silently.
    """
    if not state.dossiers:
        return state

    kept = []
    for dossier in state.dossiers:
        status, score = dedup_status(dossier)
        if status == "duplicate":
            state.skipped_duplicate += 1
            log.info("hql_dossier_dedup_skip", account_id=str(dossier.account_id),
                     score=round(score, 3))
            continue
        if status == "similar":
            dossier.dedup_note = (
                f"⚠️ {round(score * 100)}% similar to an existing dossier — "
                "possible duplicate account, confirm before publishing."
            )
        kept.append(dossier)

    state.dossiers = kept
    log.info("hql_dedup_done", kept=len(state.dossiers),
             duplicates=state.skipped_duplicate)
    return state


@traced_node("hitl_gate")
def hitl_gate_node(state: HQLState) -> HQLState:
    """G2 — people-data write gate. Founder approves each dossier before persist.

    Routes through the global HITL policy (`omerion_core.hitl.policy.gate`), which
    creates one founder review per dossier (idempotently on replay) and raises a
    single LangGraph interrupt. Decisions map back by account_id; anything not
    explicitly approved fails closed to 'rejected'.
    """
    if not state.dossiers:
        return state

    accounts_by_id = {a["account_id"]: a for a in state.accounts}
    items: list[ReviewItem] = []
    for d in state.dossiers:
        aid = str(d.account_id)
        account = accounts_by_id.get(aid, {})
        offer = d.offer_match or {}
        body = REVIEW_HEADER.format(
            account_name=account.get("name", aid),
            confidence=round(d.confidence, 2),
            quality_flags=", ".join(d.quality_flags) or "—",
            disqualifiers=", ".join(d.disqualification_flags) or "—",
            service_package=offer.get("service_package", "—"),
            demo_reference=offer.get("demo_reference", "—"),
        )
        if d.dedup_note:
            body += f"\n\n{d.dedup_note}"
        body += (
            f"\n\n**Summary**\n\n{d.summary}\n\n"
            f"**Outreach angle:** {d.outreach_angle}\n\n"
            "**Pain signals:**\n" + "\n".join(f"- {p}" for p in d.pain_signals) + "\n\n"
            "**Hooks:**\n" + "\n".join(f"- {h}" for h in d.conversation_hooks) + "\n\n"
            "**Sources:**\n" + "\n".join(f"- {u}" for u in d.source_urls)
        )
        items.append(ReviewItem(
            key=aid,
            subject=f"Dossier — {account.get('name', aid)}",
            context_md=body,
            draft_ref={"kind": "research_dossier", "account_id": aid},
        ))

    decisions = gate(
        Gate.EXTERNAL_PEOPLE_DATA_WRITE,
        items,
        agent_name=state.agent_name,
        session_id=state.session_id or "",
        correlation_id=state.correlation_id,
    )
    for d in state.dossiers:
        d.decision = decisions.get(str(d.account_id), "rejected")  # type: ignore[assignment]
    return state


@traced_node("persist_and_index")
def persist_node(state: HQLState) -> HQLState:
    # Order: Supabase write FIRST, Pinecone index SECOND. If Pinecone fails we
    # still have the dossier in the DB (re-run will idempotently re-index by
    # deterministic vector IDs). The reverse order — index first, write second
    # — leaves orphan Pinecone vectors pointing at a non-existent dossier_id
    # when the DB write fails.
    for d in state.dossiers:
        if d.decision != "approved":
            continue
        try:
            d.dossier_id = write_dossier(d)
        except Exception as exc:  # noqa: BLE001
            log.error("hql_dossier_write_failed", account_id=str(d.account_id), error=str(exc))
            continue
        try:
            d.pinecone_ids = index_dossier(d, run_date=str(state.run_date))
            if d.pinecone_ids:
                supabase.table("research_dossiers").update(
                    {"pinecone_ids": d.pinecone_ids}
                ).eq("dossier_id", str(d.dossier_id)).execute()
        except Exception as exc:  # noqa: BLE001
            log.warning("hql_dossier_index_failed", dossier_id=str(d.dossier_id), error=str(exc))
        state.dossiers_written += 1
    return state


@traced_node("emit")
def emit_node(state: HQLState) -> HQLState:
    for d in state.dossiers:
        if d.decision != "approved" or d.dossier_id is None:
            continue
        payload = {
            "account_id": str(d.account_id),
            "dossier_id": str(d.dossier_id),
            "confidence": round(d.confidence, 4),
            "offer_match": d.offer_match,
        }
        emit_event(
            EventType.DOSSIER_CREATED,
            source_agent=state.agent_name,
            payload=payload,
            correlation_id=state.correlation_id,
        )
        emit_event(
            EventType.DOSSIER_READY,
            source_agent=state.agent_name,
            payload=payload,
            correlation_id=state.correlation_id,
        )
    return state


def build():
    from omerion_core.runtime.checkpointer import get_checkpointer
    g = StateGraph(HQLState)
    g.add_node("parse_discord_intent", parse_discord_intent_node)
    g.add_node("load_priority_accounts", load_node)
    g.add_node("cognition", cognition_node)
    g.add_node("dedup_filter", dedup_filter_node)
    g.add_node("hitl_gate", hitl_gate_node)
    g.add_node("persist_and_index", persist_node)
    g.add_node("emit", emit_node)

    g.set_entry_point("parse_discord_intent")
    g.add_edge("parse_discord_intent", "load_priority_accounts")
    g.add_edge("load_priority_accounts", "cognition")
    g.add_edge("cognition", "dedup_filter")
    g.add_edge("dedup_filter", "hitl_gate")
    g.add_edge("hitl_gate", "persist_and_index")
    g.add_edge("persist_and_index", "emit")
    g.add_edge("emit", END)
    return g.compile(checkpointer=get_checkpointer())
