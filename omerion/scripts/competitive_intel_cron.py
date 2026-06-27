"""Deterministic replacement for the retired `competitive_intel` agent.

The retired agent's job was: fetch competitor RSS feeds → classify each
entry with Claude → upsert insights. The classification was a textbook
"deterministic in LLM costume" anti-pattern — entries have a title, URL,
and summary; tagging by source feed plus first-pass keyword bucketing is
sufficient for the downstream RAG retrieval that consumes them.

This script:
  1. Reads competitor slugs + feed URLs from omerion/config/agents.yaml
     under `competitive_intel.competitors`.
  2. Fetches each feed with `feedparser`.
  3. Computes a stable signal_id (sha256 of competitor + entry URL).
  4. Tags each signal deterministically via SIGNAL_KIND_RULES below.
  5. Upserts to Pinecone (namespace `competitor_intel`) for RAG.
  6. Persists a row in `competitor_signals` so the dashboard can list them.

Wired into APScheduler from omerion/main.py at a daily cadence. Safe to
run multiple times — Pinecone upserts are idempotent on signal_id and the
Supabase insert uses ON CONFLICT DO NOTHING.

Run standalone:
    cd omerion && python -m scripts.competitive_intel_cron
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any

from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.embeddings import embed
from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.scripts.competitive_intel_cron")

# Deterministic keyword → kind tagging. Order matters (first match wins).
SIGNAL_KIND_RULES: list[tuple[str, list[str]]] = [
    ("pricing", ["price", "pricing", "cost", "tier", "$", "discount", "free tier"]),
    ("product_release", ["launch", "ship", "release", "available", "now in beta", "GA"]),
    ("funding", ["raised", "Series ", "seed", "valuation", "funding"]),
    ("hiring", ["hiring", "join us", "we're hiring", "open role", "career"]),
    ("partnership", ["partner", "integration with", "now works with", "alongside"]),
    ("customer_win", ["case study", "chose", "selected", "customer story"]),
]


def _signal_id(competitor: str, url: str) -> str:
    return hashlib.sha256(f"{competitor}|{url}".encode()).hexdigest()


def _classify_kind(title: str, summary: str) -> str:
    blob = f"{title}\n{summary}".lower()
    for kind, keywords in SIGNAL_KIND_RULES:
        if any(k.lower() in blob for k in keywords):
            return kind
    return "general"


def _fetch_one_feed(competitor: str, url: str) -> list[dict[str, Any]]:
    try:
        import feedparser  # type: ignore[import]
    except ImportError:
        log.error("feedparser_missing", hint="pip install feedparser")
        return []
    try:
        parsed = feedparser.parse(url)
    except Exception as exc:  # noqa: BLE001
        log.warning("feed_parse_error", competitor=competitor, url=url, error=str(exc))
        return []
    out: list[dict[str, Any]] = []
    for entry in (parsed.entries or [])[:15]:
        entry_url = entry.get("link", "")
        title = (entry.get("title", "") or "").strip()
        if not entry_url or not title:
            continue
        summary = (entry.get("summary", "") or entry.get("description", "") or "")[:3000]
        out.append({
            "competitor": competitor,
            "title": title,
            "url": entry_url,
            "summary": summary,
            "published_at": entry.get("published") or None,
            "kind": _classify_kind(title, summary),
        })
    return out


def _persist_signal(signal: dict[str, Any]) -> None:
    """Idempotent insert; ON CONFLICT (signal_id) DO NOTHING."""
    sig_id = _signal_id(signal["competitor"], signal["url"])
    row = {
        "signal_id": sig_id,
        "competitor": signal["competitor"],
        "kind": signal["kind"],
        "title": signal["title"],
        "url": signal["url"],
        "summary": signal["summary"],
        "published_at": signal["published_at"],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("competitor_signals").upsert(row, on_conflict="signal_id").execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("signal_persist_failed", signal_id=sig_id, error=str(exc))


def _embed_to_pinecone(signal: dict[str, Any]) -> None:
    """Upsert into Pinecone namespace `competitor_intel` for RAG."""
    sig_id = _signal_id(signal["competitor"], signal["url"])
    text = f"{signal['title']}\n\n{signal['summary']}"
    metadata = {
        "competitor": signal["competitor"],
        "kind": signal["kind"],
        "url": signal["url"],
        "published_at": str(signal["published_at"] or ""),
    }
    try:
        vector = embed(text)
        pinecone_index().upsert(
            vectors=[{"id": sig_id, "values": vector, "metadata": metadata}],
            namespace="competitor_intel",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("pinecone_upsert_failed", signal_id=sig_id, error=str(exc))


def _competitor_feeds_from_settings() -> list[dict[str, Any]]:
    """Read competitor configs from agents.yaml :: competitive_intel.competitors."""
    try:
        cfg = settings.agent("competitive_intel")
    except Exception:  # noqa: BLE001 — agents.yaml may be missing the key
        log.warning("competitive_intel_config_missing")
        return []
    return list(cfg.get("competitors", []))


def run_once() -> dict[str, int]:
    """Single sweep across all configured competitors. Returns counts."""
    competitors = _competitor_feeds_from_settings()
    if not competitors:
        log.info("competitive_intel_no_competitors_configured")
        return {"fetched": 0, "persisted": 0, "embedded": 0}

    fetched = 0
    persisted = 0
    embedded = 0
    for comp in competitors:
        slug = comp.get("slug", "")
        if not slug:
            continue
        for feed in comp.get("feeds", []):
            url = feed.get("url", "")
            if not url:
                continue
            signals = _fetch_one_feed(slug, url)
            fetched += len(signals)
            for sig in signals:
                _persist_signal(sig)
                persisted += 1
                _embed_to_pinecone(sig)
                embedded += 1
            time.sleep(1.0)  # polite to feed hosts

    log.info(
        "competitive_intel_sweep_complete",
        competitors=len(competitors),
        fetched=fetched,
        persisted=persisted,
        embedded=embedded,
    )
    return {"fetched": fetched, "persisted": persisted, "embedded": embedded}


if __name__ == "__main__":
    run_once()
