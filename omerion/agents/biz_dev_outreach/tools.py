"""Tools for SEEK — Job Hunter sub-agent.

Tool families:
    Discovery (Tier B):       fetch_upwork_rss, fetch_indeed_rss, fetch_linkedin_jobs,
                              fetch_google_jobs
    Discovery (Tier S):       fetch_toptal_rss, fetch_ateam_rss,
                              fetch_braintrust_rss, fetch_contra_rss
    Discovery (Tier A):       fetch_wellfound_jobs, fetch_yc_jobs,
                              fetch_lever_board, fetch_greenhouse_board
    Profile + scoring:        load_resume, load_cover_letter_template,
                              embed_profile, score_postings, index_posting_pinecone
    Drafting + risk:          draft_application, flag_application_risks
    Persistence + send:       dedup_postings, upsert_posting, upsert_application,
                              send_application_email, queue_upwork_application
    Lifecycle:                check_ghost_applications

External APIs hit:
    SerpAPI         — Google Jobs aggregation (fetch_google_jobs)
    Firecrawl       — LinkedIn/Wellfound/YC scraping (POST /v1/scrape)
    Lever           — api.lever.co/v0/postings/{slug}            (public JSON)
    Greenhouse      — boards-api.greenhouse.io/v1/boards/{slug}/jobs (public JSON)
    Upwork/Indeed   — public RSS feeds via feedparser
    Toptal/A.Team/Braintrust/Contra — public RSS feeds via feedparser
    Pinecone        — namespace "job_postings" (founder profile + per-posting vectors)
    Supabase        — tables job_postings, job_applications
    Gmail           — application send + Message-ID capture
    Anthropic       — Sonnet for drafting, Haiku for ranking (via ClaudeRouter)
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from omerion_core.clients.supabase_client import supabase
from omerion_core.http import safe_request
from omerion_core.logging import get_logger
from omerion_core.settings import settings

from .state import ApplicationDraft, JobPosting

log = get_logger("omerion.agents.biz_dev_outreach")

_ASSETS_DIR = Path(__file__).parent.parent.parent / "assets" / "evykynn"

# Sites that match these patterns in description → flagged as scam by ranker AND flag_risks
_SCAM_PATTERNS = [
    r"\bmake\s+money\s+fast\b",
    r"\bno\s+experience\s+needed\b",
    r"\beasy\s+money\b",
    r"\bguaranteed\s+income\b",
    r"\bMLM\b",
    r"\bcrypto\s+giveaway\b",
    r"\$\$\$",
    r"\bpassive\s+income\s+system\b",
]

_BANNED_DRAFT_TOKENS = [
    r"\bI'?m\s+thrilled\b",
    r"\bI'?m\s+excited\b",
    r"\bI'?m\s+passionate\b",
    r"!",
    r"\bDAAM\b",
    r"\bCAPA\b",
    r"\bREMI\b",
    r"\bASAP\b",
    r"[\U0001F300-\U0001FAFF]",  # emoji range
]

_EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


# ─── Helpers ────────────────────────────────────────────────────────────────


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_iso() -> str:
    return date.today().isoformat()


def _safe_get(d: dict | Any, key: str, default: Any = "") -> Any:
    if isinstance(d, dict):
        return d.get(key, default)
    return getattr(d, key, default)


# ─── Profile loading ─────────────────────────────────────────────────────────


def load_resume() -> str:
    path = _ASSETS_DIR / "resume.md"
    if not path.exists():
        log.warning("seek_resume_missing", path=str(path))
        return ""
    return path.read_text()


def load_cover_letter_template() -> str:
    path = _ASSETS_DIR / "cover_letter.md"
    if not path.exists():
        log.warning("seek_cover_letter_missing", path=str(path))
        return ""
    return path.read_text()


# ─── Discovery — Upwork RSS (Tier B) ─────────────────────────────────────────


def fetch_upwork_rss(feeds: list[str]) -> list[JobPosting]:
    """Parse Upwork public RSS feeds; return normalized JobPosting list."""
    try:
        import feedparser  # type: ignore[import]
    except ImportError:
        log.error("seek_feedparser_missing", hint="pip install feedparser")
        return []

    postings: list[JobPosting] = []
    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("seek_upwork_rss_error", url=feed_url, error=str(exc))
            continue

        for entry in parsed.entries:
            url = entry.get("link", "")
            if not url:
                continue
            summary = entry.get("summary", "")
            title = entry.get("title", "No title")
            budget_low, budget_high, budget_type = _parse_upwork_budget(summary)
            postings.append(JobPosting(
                platform="upwork",
                external_id=_url_hash(url),
                kind="posting",
                title=title,
                company="Upwork client",
                description=summary[:2000],
                url=url,
                budget_low=budget_low,
                budget_high=budget_high,
                budget_type=budget_type,
                remote=True,
                posted_at=entry.get("published", None),
            ))

    log.info("seek_upwork_discovered", count=len(postings))
    return postings


def _parse_upwork_budget(summary: str) -> tuple[float | None, float | None, str]:
    """Extract budget from an Upwork RSS entry summary.

    Handles: "$50-$75/hr", "$50 to $75/hr", "$50–75 per hour",
             "Budget: $1,500", "Less than $500", "Est. budget: $5,000".
    """
    s = summary.replace("–", "-").replace("—", "-")  # normalize en/em dashes

    hourly_range = re.search(
        r"\$([0-9,.]+)\s*(?:-|to)\s*\$?([0-9,.]+)\s*(?:/|per\s*)?(?:hr|hour)",
        s, re.IGNORECASE,
    )
    if hourly_range:
        try:
            lo = float(hourly_range.group(1).replace(",", ""))
            hi = float(hourly_range.group(2).replace(",", ""))
            return lo, hi, "hourly"
        except (ValueError, AttributeError):
            pass

    hourly_single = re.search(r"\$([0-9,.]+)\s*(?:/|per\s*)?(?:hr|hour)", s, re.IGNORECASE)
    if hourly_single:
        try:
            v = float(hourly_single.group(1).replace(",", ""))
            return v, v, "hourly"
        except ValueError:
            pass

    fixed_explicit = re.search(
        r"(?:Budget|Est\.?\s*budget|Fixed[\s-]*price)\s*[:\-]?\s*\$([0-9,.]+)",
        s, re.IGNORECASE,
    )
    if fixed_explicit:
        try:
            v = float(fixed_explicit.group(1).replace(",", ""))
            return v, v, "fixed"
        except ValueError:
            pass

    less_than = re.search(r"Less\s+than\s+\$([0-9,.]+)", s, re.IGNORECASE)
    if less_than:
        try:
            v = float(less_than.group(1).replace(",", ""))
            return 0.0, v, "fixed"
        except ValueError:
            pass

    return None, None, "unknown"


# ─── Discovery — Indeed RSS (Tier B) ─────────────────────────────────────────


def fetch_indeed_rss(feeds: list[str]) -> list[JobPosting]:
    """Parse Indeed public RSS feeds."""
    try:
        import feedparser  # type: ignore[import]
    except ImportError:
        log.error("seek_feedparser_missing")
        return []

    postings: list[JobPosting] = []
    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("seek_indeed_rss_error", url=feed_url, error=str(exc))
            continue

        for entry in parsed.entries:
            url = entry.get("link", "")
            if not url:
                continue
            postings.append(JobPosting(
                platform="indeed",
                external_id=_url_hash(url),
                kind="posting",
                title=entry.get("title", "No title"),
                company=entry.get("author", ""),
                description=entry.get("summary", "")[:2000],
                url=url,
                remote="remote" in entry.get("title", "").lower()
                       or "remote" in entry.get("summary", "").lower(),
                posted_at=entry.get("published", None),
            ))

    log.info("seek_indeed_discovered", count=len(postings))
    return postings


# ─── Discovery — generic RSS for Tier S networks ─────────────────────────────


def _fetch_generic_rss(
    feeds: list[str],
    platform: str,
    default_company: str,
    rate_limit_sec: float = 2.0,
) -> list[JobPosting]:
    """Shared parser for Tier-S RSS sources (Toptal / A.Team / Braintrust / Contra).

    These feeds are simpler than Upwork — title + summary + link, no embedded
    budget metadata. Conservative rate-limiting because these are smaller hosts.

    Note: Tier-S platforms (Toptal, A.Team, Braintrust, Contra) are invite-only
    networks that do not expose public RSS feeds. These URLs will typically 404
    or return empty feeds. No action needed — SEEK will skip empty feeds and
    continue with Tier-A/B sources.
    """
    try:
        import feedparser  # type: ignore[import]
    except ImportError:
        log.error("seek_feedparser_missing")
        return []

    postings: list[JobPosting] = []
    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"seek_{platform}_rss_error", url=feed_url, error=str(exc))
            continue

        entries = parsed.entries or []
        if not entries:
            log.warning(
                f"seek_{platform}_rss_empty",
                url=feed_url,
                hint=f"{platform} likely does not expose a public RSS feed; "
                     "consider removing this URL from agents.yaml sources if it consistently returns empty",
            )
            continue

        for entry in entries:
            url = entry.get("link", "")
            if not url:
                continue
            summary = entry.get("summary", "") or entry.get("description", "")
            postings.append(JobPosting(
                platform=platform,  # type: ignore[arg-type]
                external_id=_url_hash(url),
                kind="posting",
                title=entry.get("title", "No title"),
                company=entry.get("author", default_company) or default_company,
                description=summary[:2000],
                url=url,
                remote=True,
                posted_at=entry.get("published", None),
            ))
        time.sleep(rate_limit_sec)

    log.info(f"seek_{platform}_discovered", count=len(postings))
    return postings


def fetch_toptal_rss(feeds: list[str]) -> list[JobPosting]:
    """Tier S — Toptal is invite-only; postings reach the top freelancers."""
    return _fetch_generic_rss(feeds, "toptal", "Toptal Client", rate_limit_sec=2.0)


def fetch_ateam_rss(feeds: list[str]) -> list[JobPosting]:
    """Tier S — A.Team curated mission marketplace for senior product builders."""
    return _fetch_generic_rss(feeds, "ateam", "A.Team Client", rate_limit_sec=2.0)


def fetch_braintrust_rss(feeds: list[str]) -> list[JobPosting]:
    """Tier S — Braintrust user-owned freelance network."""
    return _fetch_generic_rss(feeds, "braintrust", "Braintrust Client", rate_limit_sec=2.0)


def fetch_contra_rss(feeds: list[str]) -> list[JobPosting]:
    """Tier S — Contra commission-free independent platform."""
    return _fetch_generic_rss(feeds, "contra", "Contra Client", rate_limit_sec=2.0)


# ─── Discovery — Lever / Greenhouse JSON boards (Tier A, no scraping) ────────


def fetch_lever_board(company_slugs: list[str]) -> list[JobPosting]:
    """Fetch postings from Lever-hosted boards via api.lever.co/v0/postings/{slug}.

    Lever exposes a stable public JSON API — no scraping, no auth required.
    """
    postings: list[JobPosting] = []
    for slug in company_slugs:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            resp = safe_request("GET", url, service="lever", timeout=15.0)
            jobs = resp.json()
        except Exception as exc:  # noqa: BLE001 — after retries+breaker; skip source
            log.warning("seek_lever_error", slug=slug, error=str(exc))
            continue

        for job in jobs:
            posting_url = job.get("hostedUrl", "")
            if not posting_url:
                continue
            text = job.get("descriptionPlain") or _strip_html(job.get("description", ""))
            postings.append(JobPosting(
                platform="lever",
                external_id=str(job.get("id", _url_hash(posting_url))),
                kind="posting",
                title=job.get("text", "No title"),
                company=slug.replace("-", " ").title(),
                company_domain=f"{slug}.com",
                description=text[:3000],
                url=posting_url,
                location=_safe_get(job.get("categories", {}), "location", ""),
                remote="remote" in (text + posting_url).lower(),
                posted_at=_epoch_ms_to_iso(job.get("createdAt")),
            ))
        time.sleep(1.0)

    log.info("seek_lever_discovered", count=len(postings), slugs=len(company_slugs))
    return postings


def fetch_greenhouse_board(company_slugs: list[str]) -> list[JobPosting]:
    """Fetch postings from Greenhouse-hosted boards via boards-api.greenhouse.io.

    Public JSON API — no scraping, no auth required.
    """
    postings: list[JobPosting] = []
    for slug in company_slugs:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        try:
            resp = safe_request("GET", url, service="greenhouse", timeout=15.0)
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001 — after retries+breaker; skip source
            log.warning("seek_greenhouse_error", slug=slug, error=str(exc))
            continue

        for job in payload.get("jobs", []):
            posting_url = job.get("absolute_url", "")
            if not posting_url:
                continue
            content_html = job.get("content", "")
            text = _strip_html(content_html)
            location_obj = job.get("location", {}) or {}
            location_name = location_obj.get("name", "") if isinstance(location_obj, dict) else ""
            postings.append(JobPosting(
                platform="greenhouse",
                external_id=str(job.get("id", _url_hash(posting_url))),
                kind="posting",
                title=job.get("title", "No title"),
                company=slug.replace("-", " ").title(),
                company_domain=f"{slug}.com",
                description=text[:3000],
                url=posting_url,
                location=location_name,
                remote="remote" in (text + location_name).lower(),
                posted_at=job.get("updated_at") or job.get("first_published"),
            ))
        time.sleep(1.0)

    log.info("seek_greenhouse_discovered", count=len(postings), slugs=len(company_slugs))
    return postings


# ─── Discovery — Wellfound + YC via Firecrawl (Tier A scraped) ───────────────


def fetch_wellfound_jobs(search_urls: list[str], api_key: str) -> list[JobPosting]:
    """Scrape Wellfound (formerly AngelList Talent) job search pages via Firecrawl."""
    return _firecrawl_scrape_jobs(search_urls, api_key, platform="wellfound")


def fetch_yc_jobs(search_urls: list[str], api_key: str) -> list[JobPosting]:
    """Scrape YC Work-at-a-Startup pages via Firecrawl."""
    return _firecrawl_scrape_jobs(search_urls, api_key, platform="yc")


def _firecrawl_scrape_jobs(
    search_urls: list[str], api_key: str, platform: str,
) -> list[JobPosting]:
    if not api_key:
        log.warning(f"seek_{platform}_firecrawl_key_missing", hint="Set FIRECRAWL_API_KEY")
        return []

    base_url = settings.firecrawl_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    postings: list[JobPosting] = []

    for url in search_urls:
        try:
            resp = safe_request(
                "POST",
                f"{base_url}/v1/scrape",
                service="firecrawl",
                headers=headers,
                json={"url": url, "formats": ["markdown"]},
                timeout=30.0,
            )
            markdown = resp.json().get("data", {}).get("markdown", "")
        except Exception as exc:  # noqa: BLE001
            log.warning(f"seek_{platform}_firecrawl_error", url=url, error=str(exc))
            continue

        for block in _split_markdown_blocks(markdown):
            posting = _block_to_posting(block, platform=platform, source_url=url)
            if posting:
                postings.append(posting)
        time.sleep(1.0)

    log.info(f"seek_{platform}_discovered", count=len(postings))
    return postings


# ─── Discovery — LinkedIn Jobs (Firecrawl, Tier B) ───────────────────────────


def fetch_linkedin_jobs(search_urls: list[str], api_key: str) -> list[JobPosting]:
    """Scrape LinkedIn Jobs search pages via Firecrawl.

    Returns a mix of job postings (kind='posting') and outreach_targets
    (kind='outreach_target') when individual profile links are found.
    """
    if not api_key:
        log.warning("seek_firecrawl_key_missing", hint="Set FIRECRAWL_API_KEY")
        return []

    base_url = settings.firecrawl_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    postings: list[JobPosting] = []

    for url in search_urls:
        try:
            resp = safe_request(
                "POST",
                f"{base_url}/v1/scrape",
                service="firecrawl",
                headers=headers,
                json={"url": url, "formats": ["markdown"]},
                timeout=30.0,
            )
            markdown = resp.json().get("data", {}).get("markdown", "")
        except Exception as exc:  # noqa: BLE001
            log.warning("seek_firecrawl_error", url=url, error=str(exc))
            continue

        postings.extend(_parse_linkedin_markdown(markdown, source_url=url))
        time.sleep(1.0)

    log.info("seek_linkedin_discovered", count=len(postings))
    return postings


def fetch_google_jobs(queries: list[str]) -> list[JobPosting]:
    """Fetch job postings via SerpAPI Google Jobs endpoint.

    Google Jobs aggregates listings from Indeed, LinkedIn, Glassdoor, ZipRecruiter,
    and company career pages into one structured JSON response — broader coverage
    and more reliable than scraping LinkedIn directly via Firecrawl.

    Rate limit: conservative 2s between queries.
    Fails silently (logs warning, returns []) when SERP_API_KEY is absent.
    """
    key = settings.serp_api_key
    if not key:
        log.warning("seek_serp_key_missing", hint="Set SERP_API_KEY in .env")
        return []

    postings: list[JobPosting] = []
    for query in queries:
        try:
            resp = safe_request(
                "GET",
                "https://serpapi.com/search",
                service="serpapi",
                params={
                    "engine": "google_jobs",
                    "q": query,
                    "api_key": key,
                    "chips": "date_posted:week",   # fresh listings only
                    "hl": "en",
                    "gl": "us",
                },
                timeout=20.0,
            )
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("seek_serp_error", query=query, error=str(exc))
            continue

        for job in data.get("jobs_results", []):
            ext = job.get("detected_extensions", {}) or {}
            schedule = (ext.get("schedule_type") or "").lower()
            # Skip full-time IC roles and internships — SEEK targets contract/consulting
            if any(t in schedule for t in ("full time", "full-time", "fulltime", "intern")):
                continue

            description = job.get("description", "")
            for highlight in job.get("job_highlights", []):
                description += "\n" + "\n".join(highlight.get("items", []))

            location = job.get("location", "")
            apply_url = ""
            apply_opts = job.get("apply_options") or []
            if apply_opts:
                apply_url = apply_opts[0].get("link", "")

            postings.append(JobPosting(
                platform="google_jobs",
                external_id=_url_hash(
                    job.get("job_id") or f"{job.get('title','')}:{job.get('company_name','')}"
                ),
                kind="posting",
                title=job.get("title", ""),
                company=job.get("company_name", ""),
                description=description[:3000],
                url=apply_url,
                location=location,
                remote="remote" in location.lower() or "remote" in description.lower(),
                posted_at=ext.get("posted_at"),
            ))
        time.sleep(2.0)

    log.info("seek_google_jobs_discovered", count=len(postings), queries=len(queries))
    return postings


def _split_markdown_blocks(markdown: str, cap: int = 25) -> list[str]:
    blocks = re.split(r"\n(?=#+\s|\*\*[A-Z])", markdown)
    return blocks[:cap]


def _block_to_posting(block: str, platform: str, source_url: str) -> JobPosting | None:
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    if not lines:
        return None
    title = re.sub(r"^#+\s*|\*+", "", lines[0]).strip()
    if not title or len(title) < 5:
        return None
    company = lines[1] if len(lines) > 1 else ""
    description = " ".join(lines[2:12])
    return JobPosting(
        platform=platform,  # type: ignore[arg-type]
        external_id=_url_hash(f"{source_url}:{title}:{company}"),
        kind="posting",
        title=title,
        company=company,
        description=description[:1500],
        url=source_url,
        remote=True,
    )


def _parse_linkedin_markdown(markdown: str, source_url: str) -> list[JobPosting]:
    postings: list[JobPosting] = []
    for block in _split_markdown_blocks(markdown, cap=20):
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        title = re.sub(r"^#+\s*|\*+", "", lines[0]).strip()
        if not title or len(title) < 5:
            continue
        company = lines[1] if len(lines) > 1 else ""
        description = " ".join(lines[2:10])
        if _looks_like_person(title, company):
            postings.append(JobPosting(
                platform="linkedin_jobs",
                external_id=_url_hash(f"{source_url}:{title}:{company}"),
                kind="outreach_target",
                title=f"AI Consulting conversation with {title}",
                company=company,
                description=description[:1000],
                url=source_url,
                target_name=title,
                target_title=company,
                remote=True,
            ))
        else:
            postings.append(JobPosting(
                platform="linkedin_jobs",
                external_id=_url_hash(f"{source_url}:{title}:{company}"),
                kind="posting",
                title=title,
                company=company,
                description=description[:1000],
                url=source_url,
                remote=True,
            ))
    return postings


def _looks_like_person(title: str, context: str) -> bool:
    words = title.split()
    person_keywords = {"owner", "ceo", "founder", "president", "broker", "agent",
                       "director", "managing partner", "principal"}
    job_keywords = {"engineer", "developer", "consultant", "analyst", "coordinator",
                    "specialist", "assistant", "intern", "lead", "senior", "manager"}
    ctx_lower = context.lower()
    has_person_role = any(kw in ctx_lower for kw in person_keywords)
    has_job_role = any(kw in title.lower() for kw in job_keywords)
    looks_like_name = len(words) == 2 and all(w[0].isupper() for w in words if w)
    return looks_like_name and has_person_role and not has_job_role


# ─── HTML / time helpers ─────────────────────────────────────────────────────


def _strip_html(html: str) -> str:
    if not html:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _epoch_ms_to_iso(ms: int | None) -> str | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


# ─── Deduplication ───────────────────────────────────────────────────────────


def dedup_postings(raw: list[JobPosting]) -> list[JobPosting]:
    """Filter out postings already in the job_postings table by (platform, external_id)."""
    if not raw:
        return []
    try:
        existing = supabase.table("job_postings").select("platform,external_id").execute()
        seen: set[tuple[str, str]] = {
            (row["platform"], row["external_id"]) for row in (existing.data or [])
        }
    except Exception as exc:  # noqa: BLE001
        # Fail CLOSED: if we cannot determine which jobs were already applied to,
        # treating everything as "new" would re-send LIVE applications (duplicate
        # outbound). Skip this batch entirely; the cron re-runs and retries later.
        log.error("seek_dedup_error_failing_closed", error=str(exc))
        return []

    new_only = [p for p in raw if (p.platform, p.external_id) not in seen]
    log.info("seek_dedup", total=len(raw), new=len(new_only), skipped=len(raw) - len(new_only))
    return new_only


# ─── Scoring ─────────────────────────────────────────────────────────────────


def embed_profile(resume_text: str, cover_letter: str) -> list[float]:
    """Embed Evykynn's combined profile and upsert to Pinecone job_postings namespace."""
    from omerion_core.clients.pinecone_client import pinecone_index
    from omerion_core.llm.embeddings import embed

    combined = f"{resume_text}\n\n{cover_letter}"
    vector = embed(combined[:8000])
    pinecone_index().upsert(
        vectors=[{
            "id": "evykynn_profile",
            "values": vector,
            "metadata": {
                "agent_id": "biz_dev_outreach",
                "department": "revenue",
                "namespace": "job_postings",
                "run_date": _today_iso(),
                "content_date": _today_iso(),
                "persona": "founder",
                "market": "ai_automation_consulting",
                "source_url": "internal://evykynn_profile",
            },
        }],
        namespace="job_postings",
    )
    return vector


def score_postings(postings: list[JobPosting], profile_vector: list[float]) -> list[JobPosting]:
    """Score each posting by cosine similarity against the founder profile vector."""
    from omerion_core.llm.embeddings import embed

    for posting in postings:
        try:
            text = f"{posting.title} at {posting.company}. {posting.description[:800]}"
            posting_vector = embed(text)
            score = sum(a * b for a, b in zip(profile_vector, posting_vector))
            posting.relevance_score = max(0.0, min(1.0, float(score)))
        except Exception as exc:  # noqa: BLE001
            log.warning("seek_score_error", posting_id=str(posting.posting_id), error=str(exc))
            posting.relevance_score = 0.0
    return postings


def index_posting_pinecone(posting: JobPosting) -> str:
    """Embed + upsert posting into Pinecone job_postings namespace. Returns vector ID."""
    from omerion_core.clients.pinecone_client import pinecone_index
    from omerion_core.llm.embeddings import embed

    text = f"{posting.title} at {posting.company}. {posting.description[:800]}"
    vector = embed(text)
    vector_id = f"posting:{posting.platform}:{posting.external_id}"
    try:
        # Dual-threshold dedup: skip near-identical postings (same role, same company)
        existing = pinecone_index().query(
            vector=vector,
            namespace="job_postings",
            top_k=5,
            filter={"platform": {"$eq": posting.platform}},
            include_metadata=True,
        )
        is_apparent_dup = False
        if existing.matches:
            best = existing.matches[0].score
            if best >= 0.96:
                log.info("seek_posting_hard_dedup_skip", vector_id=vector_id, score=best)
                return vector_id
            elif best >= 0.90:
                is_apparent_dup = True
                log.info("seek_posting_soft_dedup_flag", vector_id=vector_id, score=best)

        metadata: dict = {
            "agent_id": "biz_dev_outreach",
            "department": "revenue",
            "namespace": "job_postings",
            "run_date": _today_iso(),
            "platform": posting.platform,
            "posting_id": str(posting.posting_id),
            "external_id": posting.external_id,
            "title": posting.title,
            "company": posting.company,
            "budget_type": posting.budget_type,
            "budget_low": float(posting.budget_low or 0),
            "remote": 1 if posting.remote else 0,
            "relevance_score": float(posting.relevance_score),
            "rank_score": float(posting.rank_score),
            "content_date": _today_iso(),
            "source_url": posting.url,
            "persona": "founder",
            "market": "ai_automation_consulting",
        }
        if is_apparent_dup:
            metadata["is_apparent_duplicate"] = True

        pinecone_index().upsert(
            vectors=[{"id": vector_id, "values": vector, "metadata": metadata}],
            namespace="job_postings",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("seek_pinecone_upsert_error", error=str(exc))
    return vector_id


# ─── Drafting ─────────────────────────────────────────────────────────────────


def draft_application(
    router: Any,
    posting: JobPosting,
    resume_text: str,
    cover_letter_template: str,
    *,
    run_id: Any = None,
) -> ApplicationDraft:
    """Draft a tailored application or outreach message for one posting.

    Note: passes the FULL resume + cover letter (no slicing). Sonnet's 200k
    context handles the full asset; truncation was risking dropping later
    governance / certifications sections.

    Wave 6 v1: when the Wave-6 router style filter is enabled
    (ENABLE_ROUTER_STYLE_FILTER), the LLM response may come back with
    blocked=True + style_violations=[...]. We surface those as
    `draft.hitl_flags=["style_filter"]` and a notes summary so the existing
    `hitl_review_node` Discord card shows them to the founder alongside the
    deterministic flags from `flag_application_risks`. `submit_node`
    additionally hard-skips any draft carrying the "style_filter" flag —
    belt-and-suspenders against batch-level approval missing it.
    """
    from omerion_core.llm.router import Tier

    from .prompts import APPLICATION_USER, SEEK_SYSTEM

    budget_parts = []
    if posting.budget_low is not None:
        budget_parts.append(f"${posting.budget_low}")
    if posting.budget_high is not None and posting.budget_high != posting.budget_low:
        budget_parts.append(f"${posting.budget_high}")
    budget_display = (
        "/".join(budget_parts) + f" ({posting.budget_type})" if budget_parts
        else "Not specified"
    )

    user_prompt = APPLICATION_USER.format(
        platform=posting.platform,
        kind=posting.kind,
        title=posting.title,
        company=posting.company or posting.target_name,
        url=posting.url,
        description=posting.description[:3000],
        budget_display=budget_display,
        remote="Yes" if posting.remote else "No",
        application_deadline=posting.application_deadline or "Not specified",
        required_skills=", ".join(posting.required_skills) if posting.required_skills else "Not specified",
        resume_text=resume_text,
        cover_letter_template=cover_letter_template,
    )

    # Wave 6 v1: explicit attribution kwargs so Langfuse traces show
    # `biz_dev_outreach.draft_application` (not `unknown.llm_call`), Wave-5
    # invocation log gets per-prompt attribution, and the router-level style
    # filter is force-enabled even if a future change tweaks the allowlist.
    resp = router.complete(
        tier=Tier.DEFAULT,
        system=SEEK_SYSTEM,
        prompt=user_prompt,
        max_tokens=1200,
        temperature=0.4,
        agent_name="biz_dev_outreach",
        node_name="draft_application",
        prompt_constant_name="APPLICATION_USER",
        run_id=run_id,
        human_facing=True,  # explicit at this high-stakes outbound path
    )
    text = resp.get("text", "")
    draft = ApplicationDraft(
        posting_id=posting.posting_id,
        platform=posting.platform,
        kind=posting.kind,
        rank_score=posting.rank_score,
    )
    draft.cover_letter_body = _extract_section(text, "COVER_LETTER")
    draft.proposal_body = _extract_section(text, "PROPOSAL")
    draft.outreach_message = _extract_section(text, "OUTREACH_MESSAGE")
    draft.subject_line = _extract_section(text, "SUBJECT")

    # ── Wave 6 v1: surface router-level style violations to founder HITL ──
    # `resp["blocked"]` is only present when ENABLE_ROUTER_STYLE_FILTER is on
    # AND the deterministic filter found a violation. We piggy-back on the
    # existing hitl_flags/hitl_notes plumbing — no schema change — so the
    # Discord review card already renders this alongside other risks.
    if resp.get("blocked") and resp.get("blocked_reason") == "style_filter":
        violations = resp.get("style_violations", [])
        draft.hitl_flags.append("style_filter")
        sample = "; ".join(violations[:3])
        more = f" (+{len(violations) - 3} more)" if len(violations) > 3 else ""
        note = f"Style filter blocked draft — {sample}{more}"
        draft.hitl_notes = (draft.hitl_notes + " | " + note).lstrip(" |") \
            if draft.hitl_notes else note
    return draft


def _extract_section(text: str, section: str) -> str:
    """Extract the body under a SECTION: header, stopping at the next ALL_CAPS: header."""
    start_match = re.search(rf"^{section}:\s*", text, re.MULTILINE)
    if not start_match:
        return ""
    start = start_match.end()
    next_match = re.search(r"^[A-Z_]{3,}:", text[start:], re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end].strip()


# ─── Risk flagging (HITL watchlist) ──────────────────────────────────────────


def flag_application_risks(
    draft: ApplicationDraft,
    posting: JobPosting,
    prior_drafts: list[ApplicationDraft],
    forbidden_company_keywords: list[str],
    flag_thresholds: dict[str, Any],
) -> tuple[list[str], str]:
    """Inspect a drafted application and return (flag_list, notes).

    Pure function — no LLM call, no DB writes. Deterministic checks:
      - low_rank_score      rank_score below threshold
      - missing_budget      posting has no budget at all
      - scam_signal         description matches scam patterns
      - skill_mismatch      required_skills present and not in resume
      - short_deadline      application_deadline within N days
      - duplicate_company   recent application to same company
      - forbidden_keyword   company name contains a deny-list token
      - identical_cover_text  cover_letter overlaps >threshold with another in batch
      - vague_scope         description too short / no concrete deliverables
      - off_brand_voice     draft contains banned tokens
    """
    flags: list[str] = []
    notes_parts: list[str] = []

    low_thresh = float(flag_thresholds.get("low_rank_score", 7.5))
    deadline_days = int(flag_thresholds.get("short_deadline_days", 7))
    dup_days = int(flag_thresholds.get("duplicate_company_days", 30))
    cover_overlap_thresh = float(flag_thresholds.get("identical_cover_overlap", 0.70))

    if draft.rank_score < low_thresh:
        flags.append("low_rank_score")
        notes_parts.append(f"rank {draft.rank_score:.1f} < {low_thresh}")

    if posting.budget_low is None and posting.budget_high is None:
        flags.append("missing_budget")

    desc = posting.description or ""
    for pattern in _SCAM_PATTERNS:
        if re.search(pattern, desc, re.IGNORECASE):
            flags.append("scam_signal")
            notes_parts.append(f"scam pattern: {pattern}")
            break

    if posting.required_skills:
        resume_text = (draft.cover_letter_body + " " + draft.proposal_body).lower()
        unmatched = [s for s in posting.required_skills
                     if s.lower() not in resume_text and s.lower() not in desc.lower()]
        if len(unmatched) >= max(1, len(posting.required_skills) // 2):
            flags.append("skill_mismatch")
            notes_parts.append(f"missing: {', '.join(unmatched[:3])}")

    if posting.application_deadline:
        try:
            dl = datetime.fromisoformat(posting.application_deadline.replace("Z", "+00:00"))
            if (dl - datetime.now(timezone.utc)) < timedelta(days=deadline_days):
                flags.append("short_deadline")
        except (ValueError, AttributeError):
            pass

    if posting.company and _company_recently_applied(posting.company, dup_days):
        flags.append("duplicate_company")

    company_lc = (posting.company or "").lower()
    for kw in forbidden_company_keywords:
        if kw.lower() in company_lc or kw.lower() in desc.lower():
            flags.append("forbidden_keyword")
            notes_parts.append(f"matched keyword: {kw}")
            break

    body = draft.cover_letter_body or draft.outreach_message or ""
    for prior in prior_drafts:
        if prior.draft_id == draft.draft_id:
            continue
        prior_body = prior.cover_letter_body or prior.outreach_message or ""
        if _jaccard_overlap(body, prior_body) > cover_overlap_thresh:
            flags.append("identical_cover_text")
            break

    if len(desc) < 300:
        flags.append("vague_scope")

    for pattern in _BANNED_DRAFT_TOKENS:
        if re.search(pattern, body):
            flags.append("off_brand_voice")
            notes_parts.append(f"banned token matched: {pattern}")
            break

    notes = "; ".join(notes_parts) if notes_parts else ("clean" if not flags else "see flags")
    return flags, notes


def _jaccard_overlap(a: str, b: str) -> float:
    """Token-level Jaccard similarity between two strings."""
    if not a or not b:
        return 0.0
    set_a = set(re.findall(r"\w+", a.lower()))
    set_b = set(re.findall(r"\w+", b.lower()))
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _company_recently_applied(company: str, lookback_days: int) -> bool:
    """Check if Evykynn applied to this company in the past N days."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        result = (
            supabase.table("job_applications")
            .select("application_id, posting_id, submitted_at")
            .gte("submitted_at", cutoff)
            .in_("status", ["sent", "replied"])
            .execute()
        )
        rows = result.data or []
        if not rows:
            return False
        # Resolve company via posting_id join — best-effort; fail-open returns False
        posting_ids = [r["posting_id"] for r in rows if r.get("posting_id")]
        if not posting_ids:
            return False
        postings = (
            supabase.table("job_postings")
            .select("posting_id,company")
            .in_("posting_id", posting_ids)
            .execute()
        )
        company_lc = company.lower().strip()
        for p in postings.data or []:
            if (p.get("company") or "").lower().strip() == company_lc:
                return True
    except Exception as exc:  # noqa: BLE001
        log.debug("seek_dup_company_check_failed", error=str(exc))
    return False


# ─── DB persistence ───────────────────────────────────────────────────────────


def upsert_posting(posting: JobPosting) -> str:
    """Insert or update job_postings row. Returns posting_id as string."""
    row: dict[str, Any] = {
        "platform": posting.platform,
        "external_id": posting.external_id,
        "kind": posting.kind,
        "title": posting.title,
        "company": posting.company,
        "description": posting.description,
        "url": posting.url,
        "target_name": posting.target_name,
        "target_title": posting.target_title,
        "budget_low": posting.budget_low,
        "budget_high": posting.budget_high,
        "budget_type": posting.budget_type,
        "location": posting.location,
        "remote": posting.remote,
        "posted_at": posting.posted_at,
        "application_deadline": posting.application_deadline,
        "required_skills": posting.required_skills,
        "relevance_score": posting.relevance_score,
        "rank_score": posting.rank_score,
        "rank_rationale": posting.rank_rationale,
        "pinecone_id": posting.pinecone_id,
        "last_seen_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    try:
        result = supabase.table("job_postings").upsert(
            row, on_conflict="platform,external_id"
        ).execute()
        if result.data:
            return str(result.data[0].get("posting_id", posting.posting_id))
    except Exception as exc:  # noqa: BLE001
        log.error("seek_upsert_posting_error", error=str(exc))
    return str(posting.posting_id)


def upsert_application(
    draft: ApplicationDraft,
    run_id: UUID | str,
    review_id: UUID | str | None,
) -> str:
    """Insert job_applications row. Returns application_id as string."""
    row: dict[str, Any] = {
        "posting_id": str(draft.posting_id),
        "platform": draft.platform,
        "kind": draft.kind,
        "status": "drafted",
        "cover_letter": draft.cover_letter_body,
        "outreach_message": draft.outreach_message,
        "proposal_body": draft.proposal_body,
        "subject_line": draft.subject_line,
        "resume_version": "v1",
        "review_id": str(review_id) if review_id else None,
        "run_id": str(run_id),
        "hitl_flags": draft.hitl_flags,
        "hitl_notes": draft.hitl_notes,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    try:
        result = supabase.table("job_applications").upsert(
            row, on_conflict="posting_id,resume_version"
        ).execute()
        if result.data:
            app_id = str(result.data[0].get("application_id", ""))
            draft.application_db_id = app_id or None  # type: ignore[assignment]
            return app_id
    except Exception as exc:  # noqa: BLE001
        log.error("seek_upsert_application_error", error=str(exc))
    return ""


def _hunter_find_email(domain: str, first_name: str, last_name: str) -> str | None:
    """Return a verified email via Hunter.io email-finder, or None on miss/error.

    Requires HUNTER_API_KEY in .env. Returns None silently when the key is absent
    or the API confidence score is below 50 (Hunter's "risky" threshold).
    """
    key = settings.hunter_api_key
    if not key or not domain:
        return None
    try:
        resp = safe_request(
            "GET",
            "https://api.hunter.io/v2/email-finder",
            service="hunter",
            params={
                "domain": domain,
                "first_name": first_name,
                "last_name": last_name,
                "api_key": key,
            },
            timeout=10.0,
        )
        data = resp.json().get("data") or {}
        email = data.get("email")
        confidence = data.get("score", 0)
        if email and confidence >= 50:
            return email
    except Exception as exc:
        log.warning("seek_hunter_lookup_failed", domain=domain, error=str(exc))
    return None


def _extract_recipient_email(posting: JobPosting) -> str | None:
    """Pull the first plausible email from the posting description / company field."""
    blob = " ".join(filter(None, [posting.description, posting.company, posting.target_title]))
    match = _EMAIL_REGEX.search(blob)
    if match:
        candidate = match.group(0)
        # Filter out obvious noise (image hosts, unsubscribe footers)
        if not any(bad in candidate.lower() for bad in ["unsubscribe", "noreply", "no-reply"]):
            return candidate
    return None


def send_application_email(draft: ApplicationDraft, posting: JobPosting) -> str:
    """Send application via Gmail.

    Recipient resolution:
      1. Email extracted from posting description (preferred)
      2. Founder inbox as last-resort fallback (logged as warning)
    Returns Gmail Message-ID on success, empty string on failure.
    """
    from omerion_core.clients.google_client import gmail_service

    if not draft.cover_letter_body and not draft.outreach_message:
        log.warning("seek_email_no_body", draft_id=str(draft.draft_id))
        return ""

    subject = draft.subject_line or f"AI Consulting Inquiry — {posting.title}"
    body = draft.outreach_message or draft.cover_letter_body
    recipient = _extract_recipient_email(posting)

    # For outreach targets, attempt Hunter.io lookup using the person's name
    # before falling back — gives a verified email ~60-70% of the time.
    if not recipient and draft.kind == "outreach_target" and posting.company_domain:
        name_parts = posting.target_name.split(maxsplit=1)
        first = name_parts[0] if name_parts else ""
        last = name_parts[1] if len(name_parts) > 1 else ""
        recipient = _hunter_find_email(posting.company_domain, first, last)
        if recipient:
            log.info("seek_hunter_email_found", domain=posting.company_domain, recipient=recipient)

    if not recipient:
        recipient = "omerion.io@gmail.com"
        log.warning(
            "seek_email_recipient_fallback",
            draft_id=str(draft.draft_id),
            posting_url=posting.url,
            hint="no email parsed from posting; sending to founder inbox",
        )

    try:
        import base64
        from email.message import EmailMessage

        service = gmail_service()
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = "me"
        msg["To"] = recipient
        msg.set_content(body)

        encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(
            userId="me", body={"raw": encoded}
        ).execute()
        provider_ref = result.get("id", "")

        if draft.application_db_id:
            supabase.table("job_applications").update({
                "status": "sent",
                "submitted_at": _now_iso(),
                "provider_ref": provider_ref,
                "updated_at": _now_iso(),
            }).eq("application_id", str(draft.application_db_id)).execute()

        log.info(
            "seek_email_sent",
            draft_id=str(draft.draft_id),
            recipient=recipient,
            provider_ref=provider_ref,
        )
        return provider_ref
    except Exception as exc:  # noqa: BLE001
        log.error("seek_email_send_error", draft_id=str(draft.draft_id), error=str(exc))
        return ""


def queue_upwork_application(draft: ApplicationDraft) -> None:
    """Mark Upwork application as queued_for_sender (requires Upwork API integration later)."""
    if not draft.application_db_id:
        return
    try:
        supabase.table("job_applications").update({
            "status": "queued_for_sender",
            "updated_at": _now_iso(),
        }).eq("application_id", str(draft.application_db_id)).execute()
    except Exception as exc:  # noqa: BLE001
        log.warning("seek_queue_upwork_error", error=str(exc))


def already_submitted(posting_id: UUID | str, resume_version: str = "v1") -> bool:
    """G1 idempotency guard: True if a *terminal* application already exists.

    SEEK sends real application emails. On a re-run or checkpoint replay the
    submit node would otherwise re-`upsert_application` (which resets status to
    'drafted') and then re-`send_application_email` — double-sending a live email
    to the same recipient. Treating sent/queued_for_sender/replied as terminal
    lets submit_node skip any posting that was already actioned in a prior run.
    """
    try:
        resp = (
            supabase.table("job_applications")
            .select("status")
            .eq("posting_id", str(posting_id))
            .eq("resume_version", resume_version)
            .in_("status", ["sent", "queued_for_sender", "replied"])
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception as exc:  # noqa: BLE001
        log.warning("seek_already_submitted_check_failed", error=str(exc))
        return False


def check_ghost_applications(threshold_days: int = 14) -> list[dict]:
    """Return applications sent >threshold_days ago with no reply."""
    try:
        result = supabase.table("job_applications").select("*").eq("status", "sent").execute()
        cutoff = datetime.now(timezone.utc) - timedelta(days=threshold_days)
        ghosts = []
        for row in result.data or []:
            submitted = row.get("submitted_at")
            if not submitted:
                continue
            try:
                sub_dt = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
            except ValueError:
                continue
            if sub_dt < cutoff and not row.get("replied_at"):
                ghosts.append(row)
        return ghosts
    except Exception as exc:  # noqa: BLE001
        log.warning("seek_ghost_check_error", error=str(exc))
        return []
