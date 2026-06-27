"""Tools for Market Mapper (Agent #1)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

import httpx

from omerion_core.clients.supabase_client import supabase
from omerion_core.http import PermanentHTTPError, TransientHTTPError, safe_request
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.settings import settings

from .prompts import PERSONA_CLASSIFY_SYSTEM, PERSONA_CLASSIFY_USER
from .state import MarketAccount, PersonaSeg

log = get_logger("omerion.agents.market_mapper")

_VALID_SEGMENTS: set[PersonaSeg] = {
    "ops_leader", "revenue_leader", "sme_founder", "agency_owner",
    "ecommerce_operator", "professional_services_owner", "saas_founder",
    "hr_talent_leader", "finance_ops", "unknown",
}


def target_markets() -> list[str]:
    return list(settings.agent("market_mapper").get("target_markets", []))


_TECH_SIGNALS = [
    "monday.com", "asana", "notion", "clickup", "pipedrive", "intercom",
    "freshdesk", "zendesk", "zapier", "make", "ai", "automation", "crm",
    "chatgpt", "openai", "claude", "salesforce", "hubspot", "airtable",
]

_RE_TEAM   = re.compile(r"\b(\d{1,4})\s*(?:employee|team member|staff|person)", re.I)
_RE_SIDES  = re.compile(r"\b(\d{1,5})\s*(?:client|customer|account|project)", re.I)
_RE_DOMAIN = re.compile(r"https?://(?:www\.)?([^/\s?#]+)")


def _extract_domain(url: str) -> str | None:
    m = _RE_DOMAIN.match(url or "")
    return m.group(1).lower() if m else None


def _detect_tech_signals(text: str) -> list[str]:
    t = text.lower()
    return [s for s in _TECH_SIGNALS if s in t]


def _estimate_volume(text: str) -> int | None:
    m = _RE_SIDES.search(text)
    if m:
        return int(m.group(1))
    return None


def _estimate_team_size(text: str) -> int | None:
    m = _RE_TEAM.search(text)
    if m:
        return int(m.group(1))
    return None


def _extract_role_hints(text: str) -> list[str]:
    patterns = [
        r"head\s*of\s*operations", r"vp\s*of\s*sales", r"founder",
        r"operations\s*manager", r"revenue\s*lead", r"chief\s*of\s*staff",
        r"director\s*of\s*growth", r"coo", r"ceo", r"cfo",
    ]
    found: list[str] = []
    t = text.lower()
    for p in patterns:
        if re.search(p, t):
            found.append(p.replace(r"\s*", " ").replace("\\b", "").strip())
    return found


def scrape_market(market: str) -> list[MarketAccount]:
    """Search SerpAPI for B2B companies in the target market.

    Runs four queries (ops-heavy, growth-stage, agency, professional services)
    and deduplicates by domain before returning.
    """
    key = settings.serp_api_key
    if not key:
        log.warning("market_mapper_serp_key_missing", hint="Set SERP_API_KEY in .env")
        return []

    queries = [
        f"{market} operations automation software company",
        f"{market} growth-stage B2B startup AI automation",
        f"{market} digital marketing agency AI tools",
        f"{market} professional services consulting firm technology",
    ]

    raw: list[MarketAccount] = []
    for query in queries:
        try:
            # Why safe_request: SerpAPI returns transient 5xx during peak hours and
            # 429 once the per-hour budget is approached. The token bucket and
            # exponential backoff keep this query loop within rate limits and
            # automatically recover from transient failures.
            resp = safe_request(
                "GET", "https://serpapi.com/search",
                service="serpapi",
                params={"engine": "google", "q": query, "api_key": key,
                        "num": 10, "hl": "en", "gl": "us"},
                timeout=15.0,
                attempts=3,
            )
            data = resp.json()
        except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
            log.warning("market_mapper_serp_error", market=market, error=str(exc),
                        error_class=type(exc).__name__)
            continue

        for result in data.get("organic_results", []):
            title   = (result.get("title") or "")[:120]
            link    = result.get("link") or ""
            snippet = result.get("snippet") or ""
            if not title or not link:
                continue
            domain = _extract_domain(link)
            if not domain or any(x in domain for x in ("google.", "yelp.", "indeed.", "glassdoor.")):
                continue
            raw.append(MarketAccount(
                name=title,
                domain=domain,
                website=link,
                market=market,
                source_url=link,
                volume_estimate=_estimate_volume(snippet),
                team_size=_estimate_team_size(snippet),
                tech_signals=_detect_tech_signals(snippet + " " + title),
                raw_metadata={
                    "snippet": snippet[:800],
                    "role_hints": _extract_role_hints(snippet),
                    "query": query,
                },
            ))
        # Token bucket on the "serpapi" service handles inter-query pacing now;
        # the explicit sleep here is no longer needed.

    seen_domains: set[str] = set()
    deduped: list[MarketAccount] = []
    for a in raw:
        if a.domain and a.domain not in seen_domains:
            seen_domains.add(a.domain)
            deduped.append(a)

    log.info("market_mapper_scrape_complete", market=market, found=len(deduped))
    return deduped


def classify_persona(router: ClaudeRouter, account: MarketAccount) -> PersonaSeg:
    role_hints = ", ".join(account.raw_metadata.get("role_hints", [])[:8])
    resp = router.complete(
        system=PERSONA_CLASSIFY_SYSTEM,
        prompt=PERSONA_CLASSIFY_USER.format(
            name=account.name,
            domain=account.domain or "",
            snippet=account.raw_metadata.get("snippet", "")[:1200],
            role_hints=role_hints,
        ),
        tier=Tier.FAST,
        max_tokens=10,
        temperature=0.0,
    )
    token = (resp["text"] or "").strip().lower()
    return token if token in _VALID_SEGMENTS else "unknown"  # type: ignore[return-value]


def _persona_fit(persona: PersonaSeg, account: MarketAccount) -> float:
    """Persona fit uses role_terms overlap from agents.yaml as a proxy."""
    cfg = settings.agent("market_mapper").get("persona_segments", {})
    target_terms = {t.lower() for t in cfg.get(persona, {}).get("role_terms", [])}
    if not target_terms:
        return 0.0
    hints = {h.lower() for h in account.raw_metadata.get("role_hints", [])}
    overlap = len(hints & target_terms)
    return min(1.0, overlap / max(1, min(3, len(target_terms))))


def _volume_score(account: MarketAccount) -> float:
    cfg = settings.agent("market_mapper")
    floor = float(cfg.get("min_volume_threshold", 50))
    vol = float(account.volume_estimate or 0)
    if vol <= 0:
        return 0.0
    return min(1.0, vol / max(floor, 1.0))


def _tech_maturity_score(account: MarketAccount) -> float:
    if not account.tech_signals:
        return 0.0
    # Each signal contributes 0.2 with a cap at 1.0 (5+ signals = full credit).
    return min(1.0, len(account.tech_signals) * 0.2)


def rank(account: MarketAccount, persona: PersonaSeg) -> MarketAccount:
    cfg = settings.agent("market_mapper").get("account_score_weights", {})
    vol = _volume_score(account)
    fit = _persona_fit(persona, account)
    tech = _tech_maturity_score(account)
    final = (
        vol * float(cfg.get("volume", 0.4))
        + fit * float(cfg.get("persona_fit", 0.4))
        + tech * float(cfg.get("tech_maturity", 0.2))
    )
    # When volume/team data is absent (always the case for raw SerpAPI hits),
    # do not disqualify the account — we have no structured data to measure.
    # Fall back to: qualify everything that has a name and domain.
    has_structured_data = (account.volume_estimate is not None) or (account.team_size is not None)
    if has_structured_data:
        qualifies = (
            (account.volume_estimate or 0) >= int(settings.agent("market_mapper").get("min_volume_threshold", 50))
            and (account.team_size or 0) >= int(settings.agent("market_mapper").get("min_team_size", 3))
        )
    else:
        # SerpAPI raw hit — no structured fields available. Don't disqualify
        # what we cannot measure; downstream enrichment will gate later.
        qualifies = True
    account.persona = persona
    account.volume_score = round(vol, 4)
    account.persona_fit_score = round(fit, 4)
    account.tech_maturity_score = round(tech, 4)
    account.final_score = round(final, 4)
    account.qualifies = qualifies
    return account


def upsert_market(market: str) -> UUID | None:
    resp = supabase.table("markets").upsert(
        {"name": market}, on_conflict="name"
    ).execute()
    rows = resp.data or []
    if not rows:
        return None
    return UUID(rows[0]["market_id"])


def upsert_account(account: MarketAccount, market_id: UUID | None, market_name: str = "") -> UUID | None:
    """Idempotent on (domain, market_id) per migration constraint."""
    row = {
        "name": account.name,
        "domain": account.domain,
        "website": account.website,
        "linkedin_company_url": account.linkedin_company_url,
        "persona": account.persona,
        "market_id": str(market_id) if market_id else None,
        "market": market_name,
        "volume_bucket": _bucket(account.volume_estimate),
        "team_size_bucket": _bucket(account.team_size),
        "tech_maturity_signals": account.tech_signals,
        "score": account.final_score,
        "metadata": account.raw_metadata,
        "last_refreshed_at": datetime.now(timezone.utc).isoformat(),
    }
    resp = supabase.table("accounts").upsert(row, on_conflict="domain,market_id").execute()
    rows = resp.data or []
    if not rows:
        return None
    return UUID(rows[0]["account_id"])


def _bucket(value: int | None) -> str | None:
    if value is None:
        return None
    if value < 25:
        return "lt_25"
    if value < 100:
        return "lt_100"
    if value < 500:
        return "lt_500"
    return "gte_500"


def emit_batch_ready_payload(accounts: Iterable[MarketAccount]) -> dict:
    qualified = [a for a in accounts if a.qualifies and a.account_id]
    return {
        "count": len(qualified),
        "account_ids": [str(a.account_id) for a in qualified],
        "personas": sorted({a.persona for a in qualified}),
    }
