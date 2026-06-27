"""Tools for High-Quality Lead Scraping (Agent #2).

Data-access, page-fetch, flag-clamping, and persistence helpers used by the
autonomous cognition loop (`cognition.py`) and the graph. The cognition loop
drives `_fetch_page` (as the `fetch_web_page` tool) and the model owns research
depth — there is no Python quality gate here anymore.
"""
from __future__ import annotations

import re
from typing import Iterable
from uuid import UUID

import httpx

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.embeddings import embed
from omerion_core.logging import get_logger
from omerion_core.settings import settings

from .state import Dossier

log = get_logger("omerion.agents.high_quality_lead_scraping")


def upsert_research_account(*, name: str, domain: str) -> UUID | None:
    """Upsert an `accounts` row for a Discord-triggered SOURCE research target.

    Mirrors SCOUT's `create_placeholder_account` — we need a real DB-resident
    `account_id` BEFORE `write_dossier` runs, otherwise the `research_dossiers`
    FK on `account_id` fails after Opus synthesis has already been billed.

    Idempotent on `domain` via upsert + `on_conflict`. Falls back to plain
    INSERT if the table lacks a unique constraint on `domain`. Returns None
    only on a persistent failure (caller should skip that company).
    """
    if not name:
        return None
    row: dict = {
        "name": name[:255],
        "domain": (domain or "").lower()[:255] or None,
        "status": "new",
    }
    try:
        resp = (
            supabase.table("accounts")
            .upsert(row, on_conflict="domain", returning="representation")
            .execute()
        )
        if resp.data:
            return UUID(resp.data[0]["account_id"])
    except Exception as exc:  # noqa: BLE001
        log.warning("source_account_upsert_failed", name=name, domain=domain, error=str(exc))
        try:
            resp2 = supabase.table("accounts").insert(row).execute()
            if resp2.data:
                return UUID(resp2.data[0]["account_id"])
        except Exception as exc2:  # noqa: BLE001
            log.error("source_account_insert_failed", name=name, error=str(exc2))
    return None


def load_priority_accounts(account_ids: Iterable[UUID] | None = None) -> list[dict]:
    """Pull the founder-priority accounts to research this cycle."""
    cfg = settings.agent("high_quality_lead_scraping")
    cap = int(cfg.get("max_accounts_per_cycle", 15))
    q = supabase.table("accounts").select(
        "account_id,name,domain,market_id,tier,team_size_bucket,persona,metadata"
    )
    if account_ids:
        q = q.in_("account_id", [str(a) for a in account_ids])
    else:
        q = q.order("score", desc=True)
    rows = q.limit(cap).execute().data or []
    return rows


def _fetch_page(url: str, timeout: float = 8.0) -> str:
    """Fetch a URL and return visible text, best-effort.

    Returns empty string for either:
      - 4xx/5xx response (research is best-effort; a missing /about page is not a bug)
      - transient transport errors (connect/timeout/SSL/proxy) — these are noisy
        in research contexts and the caller already has multiple sources
    Anything else (malformed URL, stdlib bugs, OOM) propagates so we can fix it.
    """
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; OmerionResearch/1.0)"},
        ) as client:
            r = client.get(url)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.PoolTimeout,
            httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as exc:
        log.warning("research_fetch_transient_failure", url=url,
                    error_class=type(exc).__name__, error=str(exc))
        return ""
    if r.status_code >= 400:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", r.text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-z#0-9]+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1200]


def _clamp_flags(values: list, key: str) -> list[str]:
    allowed = set(settings.agent("high_quality_lead_scraping").get(key, []))
    return [str(v) for v in values if str(v) in allowed]


def _clip_confidence(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def write_dossier(dossier: Dossier) -> UUID:
    # Why select-then-insert vs upsert: research_dossiers has no UNIQUE on
    # account_id alone (only on (account_id, contact_id) when contact_id is
    # set), so an `on_conflict="account_id"` upsert raises 42P10 at runtime.
    # Cheaper and clearer to look up an existing row and update in-place.
    row = {
        "account_id": str(dossier.account_id),
        "contact_id": str(dossier.contact_id) if dossier.contact_id else None,
        "summary": dossier.summary,
        "source_urls": dossier.source_urls,
        "pain_signals": dossier.pain_signals,
        "outreach_angles": [dossier.outreach_angle] if dossier.outreach_angle else [],
        "conversation_hooks": dossier.conversation_hooks,
        "offer_match": dossier.offer_match,
        "confidence_score": round(dossier.confidence, 4),
        "founder_approved": dossier.decision == "approved",
        "pinecone_ids": dossier.pinecone_ids,
    }

    existing_q = supabase.table("research_dossiers").select("dossier_id").eq(
        "account_id", row["account_id"]
    )
    if row["contact_id"] is None:
        existing_q = existing_q.is_("contact_id", "null")
    else:
        existing_q = existing_q.eq("contact_id", row["contact_id"])
    existing = existing_q.limit(1).execute().data or []

    if existing:
        dossier_id = existing[0]["dossier_id"]
        supabase.table("research_dossiers").update(row).eq(
            "dossier_id", dossier_id
        ).execute()
        return UUID(dossier_id)

    resp = supabase.table("research_dossiers").insert(row).execute()
    return UUID(resp.data[0]["dossier_id"])


def index_dossier(dossier: Dossier, run_date: str | None = None) -> list[str]:
    """Embed the summary + each pain signal + each hook into the `dossiers` namespace."""
    from datetime import date as _date
    _run_date = run_date or str(_date.today())

    # Derive offer match tier from the offer_match dict
    offer_match_tier = (dossier.offer_match or {}).get("tier", "unknown")

    # Derive confidence band
    c = dossier.confidence
    if c >= 0.90:
        confidence_band = "elite"
    elif c >= 0.60:
        confidence_band = "good"
    else:
        confidence_band = "weak"

    chunks: list[tuple[str, str]] = []
    if dossier.summary:
        chunks.append((f"{dossier.account_id}:summary", dossier.summary))
    for i, p in enumerate(dossier.pain_signals):
        chunks.append((f"{dossier.account_id}:pain:{i}", p))
    for i, h in enumerate(dossier.conversation_hooks):
        chunks.append((f"{dossier.account_id}:hook:{i}", h))
    if not chunks:
        return []

    vectors = []
    for vid, text in chunks:
        kind = vid.split(":", 2)[1]  # "summary" | "pain" | "hook"
        try:
            vectors.append({
                "id": vid,
                "values": embed(text),
                "metadata": {
                    # Mandatory schema fields
                    "agent_id": "high_quality_lead_scraping",
                    "department": "growth",
                    "namespace": "dossiers",
                    "run_date": _run_date,
                    # Agent-specific fields
                    "account_id": str(dossier.account_id),
                    "kind": kind,
                    "offer_match_tier": offer_match_tier,
                    "confidence_band": confidence_band,
                    "dossier_id": str(dossier.dossier_id) if dossier.dossier_id else "",
                },
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("dossier_embed_failed", id=vid, error=str(exc))
    if not vectors:
        return []
    pinecone_index().upsert(vectors=vectors, namespace="dossiers")
    return [v["id"] for v in vectors]
