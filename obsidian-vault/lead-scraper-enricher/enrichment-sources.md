# Lead Scraper & Enricher — Source Priority

Last updated: 2026-06-03
Maintained by: FIND (lead_scraper_enricher, Agent #3)

## Source Priority

FIND uses a cascading enrichment strategy. Each source is tried in order.
Stop once email + LinkedIn URL are both found.

| Priority | Source | Tool | What it provides | Fallback if unavailable |
|----------|--------|------|-----------------|------------------------|
| 1 | Firecrawl → LinkedIn profile URL | `firecrawl_scrape_linkedin` | Title, company, bio, connection count | httpx direct fetch |
| 2 | Hunter.io email finder | `hunter_find_email` | Verified email + confidence score (0–100) | Skip email; contact saved without email |
| 3 | httpx direct page fetch | `_fetch_page` | Homepage text, snippet signals | Empty string |

## Confidence Thresholds

| Signal | Minimum to accept |
|--------|------------------|
| Hunter.io email confidence | >= 50 (out of 100) |
| LinkedIn scrape (non-empty) | Any non-empty text (> 50 chars) |

## Rate Limits

| Service | Limit | Notes |
|---------|-------|-------|
| Firecrawl | 100 req/min (free tier) | Back off 60s on 429 |
| Hunter.io | 25 req/month (free tier) | Count carefully; upgrade for > 25 contacts/month |
| httpx | No external limit | Self-throttle to 5 concurrent max |

## PIPEDA / Privacy Note

Contacts sourced from public LinkedIn profiles and company websites only.
No purchased data lists. No scraping of personal email providers.
Hunter.io returns only professional email addresses.
