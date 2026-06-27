"""Tools for R1 Market/Tech Watcher."""
from __future__ import annotations

import time
from typing import Iterable
from uuid import UUID

import httpx as httpx

from omerion_core.clients.supabase_client import supabase
from omerion_core.llm.embeddings import embed
from omerion_core.llm.json_extraction import extract_json_object as _extract_json_object

extract_json_object = _extract_json_object
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings

from .prompts import TAG_SYSTEM, TAG_USER
from .state import RawSignal, TaggedInsight

log = get_logger("omerion.agents.r1_market_tech_watcher")

_VALID_TAGS = {"daam", "capa", "remi", "asap", "internal_os"}
_VALID_PRIORITY = {"high", "medium", "low"}

_THIN_BODY_THRESHOLD = 200
_JINA_READER_URL = "https://r.jina.ai/{url}"
_ARTICLE_TIMEOUT = 12


_MAX_RESPONSE_BYTES = 512_000  # streaming cap — enforced at socket read level


def _is_safe_url(url: str) -> bool:
    """SSRF guard: reject non-http(s) schemes and any non-global IP.

    Known limitation: TOCTOU gap — httpx re-resolves DNS on connect, so a hostile
    DNS server could flip the IP after this check. Acceptable here because URLs
    come only from developer-configured RSS feeds in agents.yaml, not user input.
    If this function is ever called with user-supplied URLs, replace with a
    custom httpx transport that pins the pre-validated IP to the TCP connection.
    """
    import ipaddress
    import socket
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = parsed.hostname or ""
    if not hostname:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None)
        for *_, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0])
            # is_global covers: private, loopback, link-local, reserved, multicast,
            # unspecified, benchmarking, documentation, shared-address ranges
            if not ip.is_global:
                return False
    except Exception:
        return False
    return True


def _stream_bytes(client: httpx.Client, url: str, **kwargs) -> bytes:
    """Stream response up to _MAX_RESPONSE_BYTES — cap enforced at socket level."""
    chunks: list[bytes] = []
    total = 0
    with client.stream("GET", url, **kwargs) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes():
            total += len(chunk)
            chunks.append(chunk)
            if total >= _MAX_RESPONSE_BYTES:
                break
    return b"".join(chunks)


def enrich_article_content(url: str) -> str:
    """Fetch full article content when RSS body is thin (<200 chars).

    Tier 1: httpx GET (handles most static pages).
    Tier 2: Jina Reader API — handles JS-gated pages.
    Returns empty string on any error — never blocks the RSS pipeline.
    """
    import re
    import urllib.parse

    if not _is_safe_url(url):
        log.debug("r1_article_url_rejected", url=url[:80])
        return ""

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; OmerionBot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(follow_redirects=False, timeout=_ARTICLE_TIMEOUT) as client:
            # Peek at status without downloading body — avoids streaming 3xx responses
            head = client.head(url, headers=headers)
            target_url = url
            if head.status_code in {301, 302, 303, 307, 308}:
                location = head.headers.get("location", "")
                if not _is_safe_url(location):
                    log.debug("r1_article_redirect_rejected", location=location[:80])
                    return ""
                target_url = location
            raw = _stream_bytes(client, target_url, headers=headers)
        text = re.sub(rb"<[^>]+>", b" ", raw).decode("utf-8", errors="replace")
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > _THIN_BODY_THRESHOLD:
            return text[:5000]
    except Exception as exc:
        log.debug("r1_article_httpx_failed", url=url[:80], error=str(exc))

    try:
        # urllib.parse.quote makes the URL an opaque path segment — prevents path injection
        safe_encoded = urllib.parse.quote(url, safe="")
        jina_url = f"https://r.jina.ai/{safe_encoded}"
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            body = _stream_bytes(client, jina_url)
        text = body.decode("utf-8", errors="replace")
        if len(text) > _THIN_BODY_THRESHOLD:
            return text[:5000]
    except Exception as exc:
        log.debug("r1_article_jina_failed", url=url[:80], error=str(exc))

    log.info("r1_article_enrich_failed", url=url[:80])
    return ""


def fetch_signals() -> list[RawSignal]:
    """Fetch raw signals from all configured RSS feeds.

    Feed URLs are defined in agents.yaml under r1_market_tech_watcher.rss_urls.
    Each entry must have: url, source_type, label.
    """
    try:
        import feedparser  # type: ignore[import]
    except ImportError:
        log.error("r1_feedparser_missing", hint="pip install feedparser")
        return []

    cfg = settings.agent("r1_market_tech_watcher")
    feed_defs: list[dict] = cfg.get("rss_urls", [])
    if not feed_defs:
        log.warning("r1_no_feeds_configured", hint="Add rss_urls list to agents.yaml r1_market_tech_watcher section")
        return []

    signals: list[RawSignal] = []
    for feed_def in feed_defs:
        url = feed_def.get("url", "")
        source_type = feed_def.get("source_type", "rss")
        if not url:
            continue
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:  # noqa: BLE001
            log.warning("r1_feed_parse_error", url=url, error=str(exc))
            continue

        entries = parsed.entries or []
        if not entries:
            log.warning("r1_feed_empty", url=url, label=feed_def.get("label", ""))
            continue

        for entry in entries[:20]:
            entry_url = entry.get("link", "")
            title = entry.get("title", "").strip()
            if not entry_url or not title:
                continue
            content = (
                entry.get("summary", "")
                or entry.get("description", "")
                or ""
            )
            if len(content) < _THIN_BODY_THRESHOLD and entry_url:
                enriched = enrich_article_content(entry_url)
                if enriched:
                    content = enriched
            signals.append(RawSignal(
                source_url=entry_url,
                source_type=source_type,  # type: ignore[arg-type]
                title=title,
                raw_content=content[:4000],
                published_at=entry.get("published") or None,
            ))
        time.sleep(1.0)

    log.info("r1_signals_fetched", total=len(signals), feeds=len(feed_defs))
    return signals


def is_relevant(signal: RawSignal) -> bool:
    keywords = [k.lower() for k in settings.agent("r1_market_tech_watcher").get("relevance_filter_keywords", [])]
    if not keywords:
        return True
    haystack = f"{signal.title}\n{signal.raw_content}".lower()
    return any(k in haystack for k in keywords)


def tag_signal(router: ClaudeRouter, signal: RawSignal) -> TaggedInsight | None:
    resp = router.complete(
        system=TAG_SYSTEM,
        prompt=TAG_USER.format(
            title=signal.title,
            source_url=signal.source_url,
            source_type=signal.source_type,
            body=signal.raw_content[:4000],
        ),
        tier=Tier.FAST,
        max_tokens=400,
        temperature=0.1,
    )
    data, _ok = extract_json_object(resp["text"])
    impact = str(data.get("impact_tag", "")).lower()
    priority = str(data.get("estimated_priority", "")).lower()
    if impact not in _VALID_TAGS or priority not in _VALID_PRIORITY:
        log.warning(
            "r1_invalid_tag_dropped",
            source_url=signal.source_url,
            impact_tag=impact or "(empty)",
            priority=priority or "(empty)",
            hint="Claude returned an off-taxonomy tag; signal will not be persisted",
        )
        return None
    return TaggedInsight(
        source_url=signal.source_url,
        source_type=signal.source_type,
        title=signal.title,
        summary=str(data.get("summary", "")).strip(),
        impact_tag=impact,  # type: ignore[arg-type]
        estimated_priority=priority,  # type: ignore[arg-type]
        raw_content=signal.raw_content,
    )


def _existing_urls(urls: Iterable[str]) -> set[str]:
    if not urls:
        return set()
    resp = supabase.table("rd_insights").select("source_url").in_("source_url", list(urls)).execute()
    return {row["source_url"] for row in (resp.data or [])}


def persist_insights(insights: list[TaggedInsight]) -> tuple[int, int]:
    if not insights:
        return 0, 0
    seen = _existing_urls(i.source_url for i in insights)
    fresh = [i for i in insights if i.source_url not in seen]
    if not fresh:
        return 0, len(insights)
    rows = [{
        "source_url": i.source_url,
        "source_type": i.source_type,
        "title": i.title,
        "summary": i.summary,
        "impact_tag": i.impact_tag,
        "estimated_priority": i.estimated_priority,
        "raw_content": i.raw_content[:6000],
        "metadata": i.metadata,
    } for i in fresh]
    resp = supabase.table("rd_insights").insert(rows).execute()
    written = len(resp.data or [])
    for row, original in zip(resp.data or [], fresh):
        original.insight_id = UUID(row["insight_id"])
    return written, len(insights) - written


_EMBED_FAILURE_HALT_THRESHOLD = 3


def index_insights(insights: list[TaggedInsight]) -> int:
    """Embed each insight summary into the `rd_insights` Pinecone namespace.

    Embedding failures are logged with full traceback. After
    _EMBED_FAILURE_HALT_THRESHOLD consecutive failures the batch halts, since
    persistent failure is almost always an OpenAI auth/quota issue that is
    going to fail every subsequent insight too.
    """
    from omerion_core.clients.pinecone_client import pinecone_index
    vectors = []
    consecutive_failures = 0
    for i in insights:
        if i.insight_id is None or not i.summary:
            continue
        try:
            vector = embed(i.summary)
            consecutive_failures = 0
        except Exception as exc:  # noqa: BLE001 — see threshold logic below
            consecutive_failures += 1
            log.warning(
                "r1_embed_failed",
                id=str(i.insight_id),
                error=str(exc),
                error_class=type(exc).__name__,
                exc_info=True,
            )
            if consecutive_failures >= _EMBED_FAILURE_HALT_THRESHOLD:
                log.error(
                    "r1_embed_batch_halted",
                    threshold=_EMBED_FAILURE_HALT_THRESHOLD,
                    indexed_so_far=len(vectors),
                    remaining=len(insights) - len(vectors) - consecutive_failures,
                )
                break
            continue
        vectors.append({
            "id": str(i.insight_id),
            "values": vector,
            "metadata": {
                "impact_tag": i.impact_tag,
                "priority": i.estimated_priority,
                "source_type": i.source_type,
            },
        })
    if not vectors:
        return 0
    pinecone_index().upsert(vectors=vectors, namespace="rd_insights")
    return len(vectors)


# ── Dual-threshold semantic dedup ─────────────────────────────────────────────
# Complements URL dedup (which only catches the same *link*): this catches the
# same *story* under different URLs — the a16z / SaaStr / Business Insider triple
# that otherwise floods R2/R3 with near-identical insights.
DEDUP_HARD_SKIP = 0.96   # ≥ → already covered by a prior insight → drop
DEDUP_SOFT_FLAG = 0.90   # ≥ (and < hard) → keep but tag near_duplicate for R3


def _cosine(a: list[float], b: list[float]) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _pinecone_nearest(vector: list[float]) -> float:
    """Highest cosine score for `vector` among prior insights in rd_insights."""
    try:
        from omerion_core.clients.pinecone_client import pinecone_index
        res = pinecone_index().query(
            vector=vector, top_k=3, namespace="rd_insights", include_metadata=False
        )
    except Exception as exc:  # noqa: BLE001 — dedup must never block the pipeline
        log.warning("r1_dedup_query_failed", error=str(exc))
        return 0.0
    matches = getattr(res, "matches", None) or []
    return max((float(getattr(m, "score", 0.0)) for m in matches), default=0.0)


def semantic_dedup(insights: list[TaggedInsight]) -> tuple[list[TaggedInsight], int]:
    """Dual-threshold dedup on insight summaries. Returns (kept, hard_skipped).

    ≥0.96 → hard-skip (drop). 0.90–0.95 → keep but tag metadata
    {near_duplicate, nearest_score} so R3 can down-weight. Compares against prior
    insights in Pinecone AND already-kept insights in THIS batch (so two
    near-identical items in one digest don't both survive).
    """
    kept: list[TaggedInsight] = []
    kept_vectors: list[list[float]] = []
    hard_skipped = 0
    for ins in insights:
        if not ins.summary:
            kept.append(ins)
            continue
        try:
            vec = embed(ins.summary)
        except Exception as exc:  # noqa: BLE001 — dedup must not block the pipeline
            log.warning("r1_dedup_embed_failed", error=str(exc))
            kept.append(ins)
            continue
        nearest = _pinecone_nearest(vec)
        for kv in kept_vectors:
            nearest = max(nearest, _cosine(vec, kv))
        if nearest >= DEDUP_HARD_SKIP:
            hard_skipped += 1
            log.info("r1_semantic_duplicate_skipped", title=ins.title[:80], score=round(nearest, 3))
            continue
        if nearest >= DEDUP_SOFT_FLAG:
            ins.metadata = {**ins.metadata, "near_duplicate": True, "nearest_score": round(nearest, 3)}
        kept.append(ins)
        kept_vectors.append(vec)
    return kept, hard_skipped
