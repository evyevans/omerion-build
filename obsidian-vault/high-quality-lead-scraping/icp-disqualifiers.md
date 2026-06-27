# ICP Disqualifiers

Last updated: 2026-06-03
Maintained by: SOURCE (high_quality_lead_scraping, Agent #2)

## Disqualifiers

These signals, if confirmed by the cognition loop, must result in immediate dossier discard.
The model must not proceed to pain signal synthesis if any of these are detected.

| Disqualifier | Detection signal | Why |
|-------------|-----------------|-----|
| Consumer business (B2C) | Product is sold to individual consumers; no business buyer | Omerion's packages require an organisational buyer |
| Public company | Stock ticker in description; "NYSE:", "NASDAQ:", "ASX:" | Enterprise sales cycle incompatible with Omerion's current GTM |
| Government / public sector | ".gov", "municipality", "department of" in description or domain | Procurement cycles and compliance requirements outside scope |
| Competitor | Company offers AI automation consulting or agent-building services | Conflict of interest |
| Micro-business (<3 team members) | Team size confirmed at 1–2 | Insufficient budget for Omerion's packages |
| No technology adoption signals | Zero tech signals, no SaaS tools mentioned, explicitly "paper-based" | ASAP/DAAM fit is near-zero |
| Non-English primary language | Company operates exclusively in non-English market | Current packages require English-language operations |

## Soft Signals (lower confidence, do not discard)

These are not hard disqualifiers but should reduce confidence by 0.10–0.20 each.

| Signal | Confidence reduction |
|--------|---------------------|
| Company website returns 404 | −0.15 |
| LinkedIn shows zero employees | −0.10 |
| Only source is the company's own blog | −0.10 |
| Job postings are all non-technical (no ops/revenue roles) | −0.10 |
