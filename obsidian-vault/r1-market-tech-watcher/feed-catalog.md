# RSS Feed Catalog — TRACK (R1 Market/Tech Watcher)

**Maintained by:** TRACK (r1_market_tech_watcher, Agent #11)  
**Source of truth:** `omerion/config/agents.yaml` → `r1_market_tech_watcher.rss_urls`  
**Last updated:** 2026-06-03  
**Purpose:** Documents all active RSS feeds, their intended coverage area, and the
`impact_tag` categories each feed primarily contributes to.

---

## Active Feeds (9 total)

| Feed | Source Type | Primary Coverage | Impact Tags |
|---|---|---|---|
| a16z Blog | blog | VC portfolio company announcements, AI trends, SaaS market insights | daam, capa, asap |
| SaaStr | blog | B2B SaaS growth, GTM, revenue operations, sales efficiency | daam, capa |
| Business Insider Tech | rss | Funding rounds, competitive landscape, enterprise AI adoption | daam, capa, asap |
| LangChain Blog | blog | LLM framework updates, agentic AI patterns, RAG improvements | internal_os |
| Anthropic Blog | blog | Claude model releases, MCP protocol updates, API changes | internal_os |
| GitHub Blog | blog | Developer tools, Copilot updates, open-source ecosystem | internal_os, asap |
| TechCrunch AI | rss | AI startup funding, product launches, enterprise AI adoption | daam, capa, remi, asap |
| The Rundown AI | newsletter | Curated daily AI news digest — broad coverage, concise format | all tags |
| Ben Evans | newsletter | Strategic analysis of technology trends and market structure | capa, asap |

---

## Coverage Gaps (known)

| Gap | Missing coverage | Priority to add |
|---|---|---|
| PropTech / Real Estate AI | No dedicated feed for remi-tagged signals | HIGH — remi has only 1–2 active accounts |
| LinkedIn/Sales Intelligence | No SDR/outreach platform news source | MEDIUM — relevant for daam |
| Supabase Blog | No Supabase changelog feed | MEDIUM — affects internal_os |
| Pinecone Blog | No Pinecone changelog feed | MEDIUM — affects internal_os |

**To add a feed:** Add an entry to `omerion/config/agents.yaml` under
`r1_market_tech_watcher.rss_urls`. Format:
```yaml
- url: "https://example.com/rss"
  source_type: blog|rss|newsletter
  label: Display Name
```

---

## Relevance Filter Keywords (agents.yaml)

Signals are pre-filtered before LLM tagging. A signal must contain ≥2 of these
keywords in the combined title + body text to proceed:

```
workflow automation, agentic AI, LangGraph, Claude, MCP, RAG, AI agents,
process automation, Supabase, Pinecone, operator efficiency, AI ROI
```

**Impact:** Signals from a16z or SaaStr that discuss general SaaS GTM without
mentioning AI automation will be filtered out before tagging. This keeps the
`rd_insights` table focused on Omerion-relevant signals.

---

## Feed Health Monitoring

Each run logs:
- `r1_signals_fetched`: total signals fetched + feed count
- `r1_feed_empty`: logged per feed that returns 0 entries
- `r1_feed_parse_error`: logged per feed that fails to parse

If a feed logs `r1_feed_empty` for 3+ consecutive runs, investigate whether
the feed URL has changed or the source has gone offline.

Each feed is limited to 20 entries per run. With 9 feeds at 20 entries each and
1-second sleep between feeds, a full fetch takes ~9 seconds.
