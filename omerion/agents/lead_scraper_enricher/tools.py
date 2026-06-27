"""Tools for Lead Scraper & Enricher.

These helpers do not know about LangGraph — they're pure functions the
nodes call. Network-touching helpers are decorated with rate-limit
token buckets and tenacity retries.
"""
from __future__ import annotations

import re
from typing import Iterable
from uuid import UUID

import httpx

from omerion_core.clients.supabase_client import supabase
from omerion_core.http import PermanentHTTPError, TransientHTTPError, safe_request
from omerion_core.llm.router import ClaudeRouter, Tier
from omerion_core.logging import get_logger
from omerion_core.rate_limit import rate_limited
from omerion_core.rate_limit.token_bucket import BUCKETS
from omerion_core.retry import transient_retry

from .state import EnrichedContact

log = get_logger("omerion.agents.lead_scraper_enricher")

_TAG_RE = re.compile(r"<[^>]+>")


def _fetch_page(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return visible text.

    Why: callers are wrapped in @transient_retry. Network/timeout errors must propagate
    so the retry decorator can see them; only treat 4xx (client errors, not retryable)
    as an empty result so we don't punish the retry loop on permanent failures.
    """
    r = httpx.get(url, timeout=timeout, follow_redirects=True,
                  headers={"User-Agent": "Mozilla/5.0"})
    if 400 <= r.status_code < 500:
        return ""
    r.raise_for_status()
    text = _TAG_RE.sub(" ", r.text)
    return " ".join(text.split())[:1200]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")




def load_accounts(account_ids: Iterable[UUID]) -> list[dict]:
    ids = [str(a) for a in account_ids]
    if not ids:
        return []
    resp = (
        supabase.table("accounts")
        .select("account_id,name,domain,market_id,tier,status")
        .in_("account_id", ids)
        .execute()
    )
    return resp.data or []


@rate_limited(BUCKETS["linkedin"])
@transient_retry(attempts=2, min_wait=2, max_wait=10)
def scrape_linkedin_text(account: dict) -> str:
    """Return the account's LinkedIn company page as markdown text (Firecrawl).

    Raw text for the autonomous enrichment loop to read and extract real
    contacts from — the model does the extraction (vs the old brittle regex).
    Falls back to a raw httpx fetch when no Firecrawl key is configured.
    """
    from omerion_core.settings import settings as _settings

    domain = account.get("domain") or ""
    company_slug = domain.split(".")[0] if domain else ""
    li_url = account.get("linkedin_url") or (
        f"https://www.linkedin.com/company/{company_slug}" if company_slug else ""
    )
    if not li_url:
        return ""

    firecrawl_key = _settings.firecrawl_api_key
    if not firecrawl_key:
        log.warning("scout_firecrawl_key_missing", hint="Set FIRECRAWL_API_KEY; falling back to raw fetch")
        return _fetch_page(li_url)

    # safe_request lets TransientHTTPError propagate to the outer @transient_retry;
    # only permanent failures fall back to a raw fetch.
    try:
        resp = safe_request(
            "POST",
            f"{_settings.firecrawl_base_url.rstrip('/')}/v1/scrape",
            service="firecrawl",
            headers={"Authorization": f"Bearer {firecrawl_key}", "Content-Type": "application/json"},
            json={"url": li_url, "formats": ["markdown"]},
            timeout=30.0,
            attempts=3,
        )
        return resp.json().get("data", {}).get("markdown", "") or ""
    except (PermanentHTTPError, httpx.HTTPError) as exc:
        log.warning("scout_firecrawl_permanent_error", url=li_url, error=str(exc),
                    error_class=type(exc).__name__)
        return _fetch_page(li_url)


@rate_limited(BUCKETS["hunter"])
def find_email_hunter(domain: str, first_name: str, last_name: str) -> str | None:
    """Look up a verified email address via Hunter.io email-finder API.

    Returns None when the key is absent, the API misses, or confidence < 50.
    Confidence threshold of 50 matches Hunter's own "risky" boundary.

    Why rate-limited at the decorator level AND service-bucket inside safe_request:
    Hunter charges per lookup; we coordinate at the function boundary so concurrent
    nodes share the same bucket even if they bypass the safe_request path (e.g.
    a future caller that does its own validation).
    """
    from omerion_core.settings import settings as _settings
    key = _settings.hunter_api_key
    if not key or not domain:
        return None
    try:
        resp = safe_request(
            "GET", "https://api.hunter.io/v2/email-finder",
            service="hunter",
            params={
                "domain": domain,
                "first_name": first_name,
                "last_name": last_name,
                "api_key": key,
            },
            timeout=10.0,
            attempts=3,
            # 404 = no email found for that name; treat as a clean miss rather than failure.
            expected_status=(200, 404),
        )
        if resp.status_code == 404:
            return None
        data = resp.json().get("data") or {}
        email = data.get("email")
        confidence = int(data.get("score") or 0)
        if email and confidence >= 50:
            log.info("scout_hunter_found", domain=domain, confidence=confidence)
            return email
    except (TransientHTTPError, PermanentHTTPError, httpx.HTTPError) as exc:
        log.warning("scout_hunter_error", domain=domain, error=str(exc),
                    error_class=type(exc).__name__)
    return None


_EXTRACT_COMPANIES_SYSTEM = (
    "You are an intent classifier for a B2B lead generation agent. "
    "The user will give you a free-text message. You must do two things:\n\n"
    "1. Determine the intent type:\n"
    "   - 'specific_target': the message mentions one or more specific company names or website domains "
    "(e.g. 'Scrape Stripe', 'Find leads at acmecorp.com', 'I need contacts from Notion and Linear').\n"
    "   - 'market_search': the message describes a market, segment, or criteria without naming specific "
    "companies (e.g. 'boutique real estate firms in Miami', 'emerging SaaS companies in Toronto').\n\n"
    "2. Return ONLY a JSON object with two keys:\n"
    "   - 'intent': either 'specific_target' or 'market_search'\n"
    "   - 'companies': a JSON array of objects with keys 'name' (string) and 'domain' (string). "
    "Infer the domain from the company name when not explicit (e.g. 'Acme Corp' → 'acmecorp.com'). "
    "This array MUST be empty ([]) when intent is 'market_search'.\n\n"
    "Examples:\n"
    "Input: 'Find me leads at stripe.com and linear.app' → {\"intent\": \"specific_target\", \"companies\": [{\"name\": \"Stripe\", \"domain\": \"stripe.com\"}, {\"name\": \"Linear\", \"domain\": \"linear.app\"}]}\n"
    "Input: 'Find emerging SaaS companies in Toronto with 10-50 employees' → {\"intent\": \"market_search\", \"companies\": []}"
)


def extract_companies_from_message(router: ClaudeRouter, message: str) -> dict:
    """Use a fast LLM call to classify intent and extract company targets from a Discord message.

    Returns a dict with keys:
      - 'intent': 'specific_target' | 'market_search'
      - 'companies': list of {'name': str, 'domain': str}
    """
    import json as _json

    resp = router.complete(
        system=_EXTRACT_COMPANIES_SYSTEM,
        prompt=message,
        tier=Tier.FAST,
        max_tokens=512,
    )
    raw = (resp.get("text") or "").strip()
    try:
        parsed = _json.loads(raw)
        if isinstance(parsed, dict) and "intent" in parsed and "companies" in parsed:
            companies = [c for c in parsed["companies"] if isinstance(c, dict) and c.get("name")]
            return {"intent": parsed["intent"], "companies": companies}
    except (_json.JSONDecodeError, ValueError):
        pass
    log.warning("extract_companies_parse_failed", raw=raw[:200])
    return {"intent": "market_search", "companies": []}  # fail safe: treat as market search


def create_placeholder_account(name: str, domain: str) -> UUID | None:
    """Return (or create) an account row for the given company.

    Uses SELECT-then-INSERT rather than ON CONFLICT because the accounts table
    has a composite unique constraint (domain, market_id), not a single-column
    one on domain. SELECT-first is idempotent: repeated Discord messages for the
    same domain return the existing account_id without creating duplicates.
    """
    if not name:
        return None
    clean_domain = (domain or "").strip().lower()[:255] or None

    # 1. Try to find an existing account with this domain.
    if clean_domain:
        try:
            resp = (
                supabase.table("accounts")
                .select("account_id")
                .eq("domain", clean_domain)
                .limit(1)
                .execute()
            )
            if resp.data:
                return UUID(resp.data[0]["account_id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("create_placeholder_lookup_failed", domain=clean_domain, error=str(exc))

    # 2. Not found — insert a minimal placeholder row.
    row: dict = {
        "name": name[:255],
        "domain": clean_domain,
        "status": "new",
    }
    try:
        resp2 = supabase.table("accounts").insert(row).execute()
        if resp2.data:
            return UUID(resp2.data[0]["account_id"])
    except Exception as exc:  # noqa: BLE001
        log.error("create_placeholder_account_insert_failed", name=name, error=str(exc))
    return None


def normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    e = email.strip().lower()
    return e if _EMAIL_RE.match(e) else None


def upsert_contact(enriched: EnrichedContact) -> UUID | None:
    """Insert-or-update on email uniqueness constraint.

    The upsert is atomic at the DB level (ON CONFLICT DO UPDATE), so no
    advisory lock is needed for dedup — concurrent sessions hitting the
    same email both land safely.
    """
    parts = (enriched.full_name or "").split(" ", 1)
    first_name = parts[0] if parts else ""
    last_name = parts[1] if len(parts) > 1 else ""
    row = {
        "account_id": str(enriched.account_id),
        "first_name": first_name,
        "last_name": last_name,
        "email": enriched.email,
        "linkedin_url": enriched.linkedin_url,
        "role": enriched.title,
        "persona": enriched.persona,
        "locale": enriched.locale,
        "status": "new",
        "source": enriched.source,
        "source_url": enriched.source_url,
    }
    resp = (
        supabase.table("contacts")
        .upsert(row, on_conflict="email")
        .execute()
    )
    if not resp.data:
        return None
    return UUID(resp.data[0]["contact_id"])


@rate_limited(BUCKETS["pinecone"])
@transient_retry(attempts=2, min_wait=1, max_wait=10, exceptions=(Exception,))
def _pinecone_upsert(record: dict, namespace: str) -> None:
    from omerion_core.clients.pinecone_client import pinecone_index as _pi
    _pi().upsert(vectors=[record], namespace=namespace)


def index_contact(contact: EnrichedContact, account_name: str = "", run_date: str | None = None) -> str | None:
    """Index one enriched contact into the growth_contacts Pinecone namespace.

    Returns the vector ID on success, None on failure.
    """
    from datetime import date as _date
    from omerion_core.llm.embeddings import embed

    _run_date = run_date or str(_date.today())

    tech_signals = getattr(contact, "tech_signals", []) or []
    text = (
        f"{contact.full_name} | {contact.title or 'unknown title'} | "
        f"{account_name} | {contact.persona} | "
        f"tech: {', '.join(tech_signals) or 'unknown'}"
    )

    vid = f"{contact.contact_id}:profile"
    try:
        record = {
            "id": vid,
            "values": embed(text),
            "metadata": {
                "agent_id": "lead_scraper_enricher",
                "department": "growth",
                "namespace": "growth_contacts",
                "run_date": _run_date,
                "contact_id": str(contact.contact_id),
                "account_id": str(contact.account_id) if contact.account_id else "",
                "persona": contact.persona or "unknown",
                "email_confidence": float(contact.email_confidence or 0.0),
                "has_linkedin": bool(contact.linkedin_url),
            },
        }
        _pinecone_upsert(record, "growth_contacts")
        return vid
    except Exception as exc:
        log.warning("contact_index_failed", contact_id=str(contact.contact_id), error=str(exc))
        return None
