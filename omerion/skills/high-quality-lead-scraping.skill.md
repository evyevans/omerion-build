---
name: high_quality_lead_scraping
version: 2.0.0
tier: A
agent_number: 2
graph: agents.high_quality_lead_scraping.graph:build
triggers:
  - cron                          # daily — cadence in config/agents.yaml (high_quality_lead_scraping.schedule), via the runtime scheduler through the full run lifecycle (create_run → execute_run)
  - event:account.batch.ready     # MAP emits when qualifying accounts are discovered
  - discord                       # reactive: founder posts in #leads
events_consumed:
  - account.batch.ready
events_emitted:
  - contact.enriched              # downstream: RATE (icp-scoring) consumes this
hitl: true                        # G2 gate — founder approves enrichment batch before dossiers are persisted
model_tier: DEFAULT               # Claude Sonnet for deep research cognition
discord_channel: leads
rate_limits:
  - firecrawl
  - serpapi
  - anthropic
concurrency:
  lock: pg_advisory_lock
  key: account_domain
---

# SOURCE — High-Quality Lead Scraper (Agent #2)

## Identity & Scope
SOURCE is Omerion's elite deep-dive account researcher and qualifier. For every
raw company domain handed to it, SOURCE independently researches the company
across the web, evaluates it against Omerion's ICP, identifies specific
operational pain points, maps them to Omerion service packages, and outputs a
highly structured, confidence-scored `CompanyDossier`. SOURCE does **not** scrape
individual contacts or emails (that is FIND / Agent #3). It does **not** send
emails or update the CRM directly. Its output feeds FIND via the
`contact.enriched` event.

- **You DO:** Research companies, identify pain signals, score confidence,
  map to Omerion Service Packages, and output account-level dossiers.
- **You DO NOT:** Scrape individual contacts/emails (FIND). Send emails (REACH/GROW).
  Update the CRM directly. Approve your own dossiers.

## Omerion Business Context & ICP (Ideal Customer Profile)
Omerion builds enterprise-grade, multi-agent AI systems for B2B operators.
**Target Market:** B2B companies, Real Estate firms, high-volume sales
organizations, agencies. Size: 10–200 employees, $1M+ Revenue.

### Omerion Core Products (Service Packages & Demos)
When analyzing a company's pain points, map them to the most relevant Operator
Archetype and Omerion product:

| Package | Demo | Archetype | Pain Signals |
|---------|------|-----------|-------------|
| `revenue_acceleration_engine` | `DAAM` | `high_velocity` | Deals dying in silence, poor follow-up, scattered pipelines, hiring SDRs, slow speed-to-lead |
| `ops_intelligence_layer` | `CAPA` | `system_multiplier` | Executives wasting time on CRM updates, manual calendar management, fragmented ops workflows |
| `research_decision_stack` | `REMI` | `capital_allocator` | Manual property/market research, slow analysis, missing fast deals (Real Estate ONLY) |
| `process_automation_suite` | `ASAP` | `system_multiplier` | High-volume ops lacking accountability, missing revenue targets, doc generation bottlenecks |

## Trigger & Input Contract
- **Primary:** daily cron (`config/agents.yaml → high_quality_lead_scraping.schedule`,
  default `0 7 * * *`), registered in `scheduler.py` through the full run lifecycle.
- **Event:** `account.batch.ready` from MAP (Agent #1) pre-populates
  `state.candidate_account_ids` so SOURCE researches freshly discovered accounts
  immediately rather than waiting for the next daily run.
- **Reactive:** founder posts in `#leads` (e.g. "research acmecorp.com") — parsed
  to a target domain + custom instructions.
- **Input:** `accounts` table rows with `status IN (new, needs_refresh)`, limited
  to `max_accounts_per_run` (default: 20 from `agents.yaml`).

## Reasoning Chain (8-node LangGraph graph)

```
load_accounts
  → check_disqualification    (deterministic pre-filter)
  → rag_check                 (Pinecone — skip already-dossier'd accounts)
  → research                  (multi-source web research via tools)
  → analyze                   (Claude Sonnet — pain signal extraction + package mapping)
  → score_confidence          (deterministic confidence anchoring)
  → hitl_review               (G2 gate — founder approves dossier batch)
  → hitl_wait                 ← interrupt(); PostgresSaver checkpoints here
  → persist_and_emit          (write dossiers + emit contact.enriched)
```

### Node 1 — `load_accounts`
- **Purpose:** Pull accounts eligible for deep research.
- **Tools called:** `load_accounts(candidate_account_ids | None, limit=20)`
- **Query:** `accounts` where `status IN (new, needs_refresh)` AND
  `last_researched_at IS NULL OR last_researched_at < now() - refresh_interval_days`.
  When `candidate_account_ids` is set (event-triggered), uses `.in_()` filter.
- **Output:** `state.accounts` (list of account dicts with domain, market, persona)
- **Failure mode:** Supabase error → exception propagates, run fails. Cron retries next day.

### Node 2 — `check_disqualification`
- **Purpose:** Deterministic pre-filter. Remove accounts that match any
  disqualification criteria before spending tool calls or LLM tokens.
- **Disqualification triggers (any one → `is_qualified = false`, halt research):**
  - `inactive`: Website returns 404, domain parked, or no updates in >12 months.
  - `already-client`: Known existing client (checked against `clients` table).
  - `recent-acquisition`: Company was acquired within the last 12 months.
  - `retiring`: Founder/CEO publicly announced retirement or winding down.
  - `duplicate`: Domain already has a dossier with `confidence_score >= 0.80`
    created within `refresh_interval_days`.
- **Output:** `state.qualified_accounts`, `state.disqualified` (list with reasons)
- **Failure mode:** pure function — cannot fail.

### Node 3 — `rag_check`
- **Purpose:** Query Pinecone `dossiers` namespace to check if a high-quality
  dossier already exists for this domain. Prevents redundant deep research.
- **Tools called:** `query_dossier_history(domain)` → cosine similarity against
  prior dossier summaries.
- **Logic:** if prior dossier exists with `confidence_score >= 0.85` and
  `created_at < refresh_interval_days`, skip this account and copy the prior
  dossier data into `state.cached_dossiers`.
- **Output:** `state.accounts_to_research` (minus cached), `state.cached_dossiers`
- **Failure mode:** Pinecone unavailable → `rag_check` skips dedup, all accounts
  proceed to research. Log `source_rag_check_failed`.

### Node 4 — `research`
- **Purpose:** Multi-source web research per account. Gather hard evidence —
  do not guess.
- **Per-account research sequence (tool call hierarchy):**
  1. **Company Homepage** (`fetch_page(domain)`) — core value proposition, product/service.
  2. **About/Team Page** (`fetch_page(domain + "/about")`) — company size, leadership,
     founding date.
  3. **LinkedIn Company Page** (`scrape_linkedin_page(linkedin_url)`) — recent growth,
     hiring signals, employee count, recent posts.
  4. **Careers Page** (`fetch_page(domain + "/careers")`) — hiring velocity, role types
     (ops, automation, SDR = strong pain signals).
  5. **News/Press** (`search_web(company_name + " funding OR acquisition OR launch")`) —
     recent funding, expansions, product launches.
  6. **Tech Stack Detection** (`search_web(company_name + " site:stackshare.io OR
     site:builtwith.com")`) — CRM, automation tools, tech maturity signals.
- **Budget:** Maximum **8 tool calls per account**. Prioritize homepage + LinkedIn +
  careers. If Firecrawl fails, fall back to `fetch_page` (standard HTTP).
- **Output:** `state.research_data[domain]` (dict of source → raw content per account)
- **Failure mode:** per-tool errors are caught. If homepage returns 404, mark
  `is_qualified = false` with reason `inactive`. If LinkedIn is blocked, continue
  with remaining sources. If ALL tools fail for an account, mark
  `research_status = "failed"` and skip to next account.

### Node 5 — `analyze`
- **Purpose:** LLM synthesis of raw research into a structured `CompanyDossier`.
- **Tools called:** `analyze_account(router, research_data, account)` →
  Tier.DEFAULT (Claude Sonnet), `max_tokens=1200`, `temperature=0.2`
- **Per-account prompt:** `DOSSIER_SYSTEM` + formatted `DOSSIER_USER` with all
  gathered research data. The model must:
  1. Identify specific operational pain points (not generic industry problems).
  2. Map each pain point to a specific Omerion service package.
  3. Score each pain signal's strength (explicit vs. inferred).
  4. Select exactly one `recommended_service_package` and one `demo_reference`.
  5. Write a `research_summary` in business-operator language (not SaaS jargon).
- **Output:** `state.dossiers` (list of `CompanyDossier` with all fields populated)
- **Failure mode:** LLM parse error → log `source_analyze_failed` with domain.
  Mark `dossier.confidence_score = 0.0`, `dossier.is_qualified = false`. Continue
  to next account. Do not block the batch.

### Node 6 — `score_confidence`
- **Purpose:** Deterministic confidence anchoring based on evidence depth.
- **Scoring rules (post-LLM, deterministic override):**
  - **0.90–1.00 (Elite):** 4+ sources verified. Explicit pain signals found
    (e.g., "hiring 10 SDRs", "struggling with manual pipeline"). Perfect offer match.
  - **0.60–0.89 (Good):** 2–3 sources verified. Strong inferred pain based on
    industry averages and growth stage.
  - **0.30–0.59 (Weak):** Homepage only. Generic value prop. Weak offer match.
  - **< 0.30 (Discard):** Barely functional site, no clear business model.
- **Source count bonus:** `sources_verified = len([s for s in research_data if s.content])`
  → `confidence_score = min(confidence_score, sources_verified * 0.22)` (cap prevents
  inflating weak research with many empty sources).
- **Output:** `state.dossiers` with calibrated `confidence_score` values.
- **Failure mode:** pure function — cannot fail.

### Node 7 — `hitl_review` + `hitl_wait`
- **Purpose:** Build the batch review card and create a `founder_review_queue` row.
  Suspend graph at `interrupt()`. PostgresSaver checkpoints `SourceState`.
- **Tools called:** `create_founder_review_task(...)` from `omerion_core/hitl/review.py`
- **Card includes:** dossier count, avg confidence, package distribution; per-dossier:
  domain, persona, package, confidence, top 2 pain signals, research summary (first 300 chars).
- **Replay guard:** returns early if `state.decision in ("approved", "rejected")`
  before calling `interrupt()`.
- **Output:** `state.decision` ∈ `{"approved", "rejected"}`, optional
  `state.scratch["decision_notes"]`
- **Skips:** if `state.dossiers` is empty.

### Node 8 — `persist_and_emit`
- **Purpose:** Write approved dossiers to Supabase and emit events.
- **Per-dossier sequence:**
  1. `upsert_dossier(dossier)` → `account_dossiers` table, idempotency key `(domain)`.
  2. `update_account_status(domain, "researched")` → update `accounts.status`.
  3. `embed_dossier(dossier)` → Pinecone `dossiers` namespace with summary vector.
- **Events emitted:** `contact.enriched` per qualified dossier with
  `{account_id, domain, persona, confidence_score, recommended_service_package}`.
- **Skips:** if `state.decision != "approved"`.
- **Output:** `state.dossiers_persisted`, `state.dossiers_skipped`

## Output Contract
- **Supabase `account_dossiers`:** upserted per approved dossier with `domain`,
  `is_qualified`, `confidence_score`, `disqualification_reason`, `quality_flags`,
  `business_model`, `estimated_size`, `pain_signals`, `recommended_service_package`,
  `demo_reference`, `research_summary`, `sources_used`.
- **Supabase `accounts`:** `status` updated to `"researched"`, `last_researched_at` set.
- **Pinecone `dossiers` namespace:** one vector per dossier summary (for RAG dedup).
- **Events emitted:** `contact.enriched` per qualified dossier.
- **State counters logged:** `dossiers_persisted`, `dossiers_skipped`,
  `disqualified_count`, `cached_from_rag`.

## Golden Dossier Output

A 10/10 `CompanyDossier` — what every output should aspire to:

```json
{
  "domain": "acmecorp.com",
  "is_qualified": true,
  "confidence_score": 0.92,
  "disqualification_reason": null,
  "quality_flags": ["tech-forward", "actively-hiring", "growing-team"],
  "business_model": "b2b_saas",
  "estimated_size": "50-200",
  "pain_signals": [
    "Currently hiring 5+ Account Executives, indicating high pipeline volume but potential follow-up leakage.",
    "Using fragmented tech stack (HubSpot, DocuSign, Outreach) requiring manual data entry between systems.",
    "CEO posted on LinkedIn about 'losing deals to slow response times' — explicit speed-to-lead pain."
  ],
  "recommended_service_package": "revenue_acceleration_engine",
  "demo_reference": "DAAM",
  "operator_archetype": "high_velocity",
  "research_summary": "Acme Corp is a 75-person B2B SaaS company scaling their sales org rapidly — 5 AE roles open — but relies on a fragmented HubSpot+DocuSign+Outreach stack with manual handoffs. CEO publicly flagged speed-to-lead as the #1 bottleneck. DAAM's automated follow-up and SLA enforcement is a direct fit to stop deals dying at the table.",
  "sources_used": [
    "https://acmecorp.com",
    "https://acmecorp.com/careers",
    "https://linkedin.com/company/acme-corp",
    "https://techcrunch.com/2026/04/acme-series-b"
  ],
  "sources_verified": 4,
  "researched_at": "2026-06-03T07:00:00Z"
}
```

## Guardrails
- **NEVER fabricate pain signals.** Every pain signal must cite a specific source
  (URL, LinkedIn post, job listing). If no pain signal is found, set
  `confidence_score < 0.30` — do not invent problems.
- **NEVER guess the service package.** Package selection must be grounded in at
  least one explicit or strongly inferred pain signal. If ambiguous, set
  `recommended_service_package = null` and flag for founder review.
- **Maximum 8 tool calls per account.** Prioritize depth over breadth. Homepage +
  LinkedIn + careers is the minimum viable research.
- **Cost ceiling:** `max_accounts_per_run * 8 tool calls * avg_cost` must stay
  within the daily budget. Monitor via `source_daily_cost_usd` metric.

## Stop Conditions
- **No accounts loaded:** all downstream nodes short-circuit. Run completes normally.
- **All accounts disqualified at Node 2:** `research` and downstream nodes skip.
  No HITL card created. Log `source_all_disqualified`.
- **All dossiers below confidence threshold:** persist with `is_qualified = false`.
  Do not suppress — emit normally so RATE can still score contacts from these accounts
  at a reduced weight.
- **Batch rejected by founder:** `persist_and_emit` logs rejection and returns.
  Nothing written.

## Idempotency Rules
- `account_dossiers` upsert on `(domain)` — re-running SOURCE for the same domain
  safely overwrites the prior dossier with fresher data.
- `accounts.last_researched_at` is always overwritten on persist — use it to detect
  stale accounts.
- Pinecone `dossiers` namespace uses `dossier:{domain_hash}` as vector ID — safe
  to re-embed.
- `contact.enriched` events use natural key `contact.enriched:{account_id}` for
  dedup — the broker silences duplicate events within the dedup window.

## Fallback Protocol

| Failure | Fallback |
|---------|----------|
| Firecrawl `fetch_page` fails (timeout, 403, 500) | Fall back to standard HTTP via `httpx.get`. If that also fails, skip source and continue. |
| LinkedIn page blocked (429 or bot detection) | Use `search_web(company_name + " LinkedIn")` to find third-party summaries. |
| `search_web` returns zero results | Continue with available sources. Reduce `confidence_score` proportionally. |
| Sonnet `analyze` call fails (API error) | Mark `confidence_score = 0.0`, `is_qualified = false`. Log `source_analyze_failed`. Continue to next account. |
| Sonnet returns unparseable JSON | Re-prompt once with schema reminder. If second attempt fails, same fallback as above. |
| Anthropic rate limit (429) | Apply backoff `[4, 15, 60]` seconds. After 3 retries, mark account `research_status = "rate_limited"`. |
| Supabase persist fails | Log `source_persist_failed` with domain. Skip that dossier. Cron retries next day. |
| Pinecone embed fails | Dossier is still written to Supabase. Embedding retried on next run via upsert. |

## Model Tier Rationale
**Claude Sonnet (Tier.DEFAULT) for `analyze`:** Deep research synthesis requires
connecting signals across 4–6 web sources, identifying implicit pain points from
hiring patterns and tech stack signals, and mapping them to the correct service
package. Haiku misses inferred pain signals and produces generic summaries that
don't differentiate between accounts. Opus is unnecessary — SOURCE produces
structured JSON dossiers (not persuasive prose), and the confidence anchoring in
Node 6 is fully deterministic.

**No Haiku anywhere:** SOURCE's per-account research is the foundation of the
entire pipeline. A misclassified account cascades errors through FIND → RATE →
REACH → GROW → PAIR. Quality at this stage pays compound dividends downstream.

## Observability
- **Langfuse trace prefix:** `source.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `dossiers_persisted` per run (target: ≥ 10 when accounts exist)
  - `avg_confidence_score` per run — below 0.50 means research sources are degrading
  - `disqualification_rate` = disqualified / loaded — rising rate means MAP is sending lower-quality accounts
  - `sources_verified_avg` — average source count per dossier; below 2.0 means tools are failing
  - `package_distribution` — which packages are recommended most (signals ICP alignment)
  - `rag_cache_hit_rate` — how often prior dossiers are reused (cost savings indicator)
  - `tool_failure_rate` — Firecrawl/LinkedIn/search failures per run

## Config Reference
All runtime config under `config/agents.yaml → high_quality_lead_scraping`:

| Key | Purpose |
|-----|---------|
| `schedule` | Cron cadence (default: `0 7 * * *`) |
| `max_accounts_per_run` | Cap on accounts processed per run (default: 20) |
| `refresh_interval_days` | Days before a dossier is considered stale (default: 30) |
| `min_confidence_threshold` | Minimum confidence to mark `is_qualified = true` (default: 0.30) |
| `max_tool_calls_per_account` | Budget cap on tool calls (default: 8) |
| `rag_cache_confidence_threshold` | Min confidence to reuse a cached dossier (default: 0.85) |
