---
name: market-mapper
tier: C
agent_number: 1
graph: agents.market_mapper.graph:build
triggers:
  - cron                     # weekly — cadence in config/agents.yaml (market_mapper.schedule), via the runtime scheduler through the full run lifecycle (NOT skill-frontmatter direct-dispatch)
  - manual                   # on-demand: the enricher delegates here for market_search intent
events_consumed: []
events_emitted:
  - account.batch.ready
hitl: false                  # accounts are companies, not people — the G2 gate is downstream in the enricher's contact writes
model_tier: FAST            # Claude Haiku — classify node, max_tokens=10
rate_limits:
  - googlemaps
  - linkedin
---

# MAP — Market Mapper (Agent #1)

## Identity & Scope
MAP owns the top-of-funnel account discovery step. It discovers, classifies,
scores, and persists target accounts across Omerion's target industries. MAP does
**not** enrich individual contacts (that is FIND/Agent #3). Its output feeds
FIND via the `account.batch.ready` event.

## Trigger & Input Contract
- **Trigger:** Monday cron, 06:00 local.
- **Input:** `config/agents.yaml → market_mapper.target_markets` (list of
  market strings, e.g. "Greater Toronto Area", "Phoenix").
- **No upstream events consumed.**

## Reasoning Chain (5-node LangGraph graph)

### Node 1 — `seed_markets`
Load `target_markets` from settings. If empty, halt immediately and log
`market_mapper_no_markets`. Do not proceed.

### Node 2 — `scrape`
For each market string, call the scrape adapter (SerpAPI / directory /
Google Maps). Each adapter must return `MarketAccount` with at minimum:
`name`, `market`, `source_url`. Append all results to `state.candidates`.
If a market returns zero results, log a warning but continue to next market.

### Node 3 — `classify`
For each candidate, call Claude Haiku with `PERSONA_CLASSIFY_SYSTEM`.
Output must be exactly one token from the 9-persona taxonomy:
`ops_leader | revenue_leader | sme_founder | agency_owner | ecommerce_operator |
professional_services_owner | saas_founder | hr_talent_leader | finance_ops | unknown`

Tier-1 personas are priority targets. `unknown` is valid — do not guess.
Use `temperature=0.0`, `max_tokens=10`. Any response not in the allowed
set is coerced to `"unknown"`.

### Node 4 — `rank`
Compute `final_score` deterministically:
```
final_score = volume_score × 0.35 + persona_fit × 0.45 + tech_maturity × 0.20
```
`qualifies = True` only when:
- `volume_estimate >= min_volume_threshold` (default: 50)
- `team_size >= min_team_size` (default: 3)

Accounts where `qualifies = False` are skipped at upsert and incremented
in `state.accounts_skipped_threshold`. Do not delete them from candidates.

### Node 5 — `upsert`
Write qualifying accounts to Supabase `accounts` table.
**Idempotency key:** `(domain, market_id)` — enforced by DB constraint
`on_conflict="domain,market_id"`. Always upsert, never insert-only.
Set `last_refreshed_at` to `now()` on every upsert.

### Node 6 — `emit`
Group qualifying accounts by market. For each market with ≥1 qualifying
account, emit `ACCOUNT_BATCH_READY` with `{count, account_ids, personas}`.
If a market has zero qualifying accounts, emit nothing for that market — do
not emit an empty batch.

## Output Contract
- **Supabase table:** `accounts` — upserted rows with `persona`, `score`,
  `volume_bucket`, `team_size_bucket`, `tech_maturity_signals`, `market_id`.
- **Supabase table:** `markets` — upserted rows on `name` conflict.
- **Event emitted:** `account.batch.ready` per market with qualifying accounts.
- **State counters logged:** `accounts_upserted`, `accounts_skipped_threshold`.

## Qualified Prospect Thresholds (2026 Benchmarks)
Accounts below all thresholds for their persona are skipped at rank step
regardless of final_score:

| Persona | Min Team Size | Min Revenue Signal | Additional Signal |
|---------|--------------|-------------------|-------------------|
| sme_founder | 5–200 employees | $500K+ ARR or revenue signal | active hiring or funding mention |
| ops_leader | 20+ employees | established org | ops/automation budget signal |
| revenue_leader | 10+ employees | active pipeline | CRM or outreach tool visible |
| agency_owner | 3–50 employees | client-service model | 2+ years in business |
| ecommerce_operator | — | $100K+ GMV signal | Shopify/WooCommerce/DTC presence |
| saas_founder | 5–100 employees | product launched | automation or growth-stack signal |

## Data Source Priority (per `config/agents.yaml → market_mapper.data_sources`)
1. Apollo.io / Hunter.io — company + contact discovery with email verification
2. LinkedIn Sales Navigator — role/title signals, company size, recent hires (TOS-compliant only)
3. SerpAPI / Google Maps Places API — geo-tagged business locations and reviews
4. Wellfound / Crunchbase — funding signals, startup metadata, growth-stage indicators
5. Clearbit / FullContact — firmographic enrichment (industry, revenue estimate, tech stack)
6. Outscraper — public business directories + Google My Business aggregation

**Canadian market note (PIPEDA compliance):** Canadian privacy law applies.
Do not persist personal identifiers (email, phone, individual name) from
Canadian-sourced records without documented consent signal. Persist company-level
data only. Log `market_mapper_pipeda_flag` on any Canadian row with personal data.

## Tech-Forward Detection Heuristics
Score `tech_maturity` using signal count × 0.2 (cap at 1.0). Require ≥3
signals before classifying as "tech-forward" — single signals are vanity indicators.

| Signal | Score |
|--------|-------|
| AI/automation mention in LinkedIn posts (last 90 days) | +3 |
| Visible tech stack on website (Zapier, Make, HubSpot, Salesforce, Notion) | +2 |
| AI chatbot or automation widget on website | +2 |
| G2 / Product Hunt listing or recent award with tech angle | +3 |
| Recent press or funding mention with tech/AI angle | +2 |
| Hiring ops/automation/AI role (LinkedIn job posts) | +2 |
| Google Maps or review mention of "systems" or "tech-forward" | +1 |
| Active in automation community (Product Hunt, Indie Hackers, AI forums) | +1 |

Tech-resistant signals (reduce `tech_maturity` toward 0.0):
- No website or static HTML-only site
- No social media activity in last 12 months
- Manual language throughout ("call us," no digital tool mention)

**HITL sampling:** 10% of newly upserted accounts are randomly flagged for
`status = "sample_review"` in Supabase. Founder reviews these weekly to
calibrate classification accuracy before full pipeline progression.

## Stop Conditions
- **Empty market list:** halt at `seed_markets`, log `market_mapper_no_markets`.
- **All candidates score below threshold:** complete run normally, emit nothing,
  log `market_mapper_zero_qualified`. Do not raise an exception.
- **Scraper adapter raises exception:** log error with market name, continue
  to next market. Do not abort the full run.
- **GTA rows contain personal identifiers:** flag with `market_mapper_pipeda_flag`,
  strip personal fields before upsert, continue.

## Idempotency Rules
- Upsert on `(domain, market_id)` — running MAP twice in one week is safe.
- `last_refreshed_at` is always overwritten; use it to detect stale accounts.
- Never hard-delete accounts; stale ones age out via `last_refreshed_at` queries.

## Fallback Protocol
- **Supabase write fails:** log `market_mapper_upsert_error` with domain and
  error. Skip that account. Continue to next. Do not retry inline — Celery
  beat will re-run on next weekly cycle.
- **LLM classify call fails:** mark `persona = "unknown"`, `final_score = 0.0`,
  `qualifies = False`. Log `market_mapper_classify_error`. Never block the run.
- **Rate limit (429) on scraper:** apply exponential backoff from
  `global.default_rate_limit_backoff_seconds: [4, 15, 60]`. After 3 retries,
  log and skip that market.

## Model Tier Rationale
**Claude Haiku (FAST tier):** MAP calls the LLM once per candidate account for
persona classification. Output is a single token (≤10 tokens). Volume is high
(hundreds of accounts per run). Haiku delivers 10–30× cost savings vs. Sonnet
with identical classification accuracy for this deterministic, zero-creativity
task.

**Escalation rule (Grok-validated):** If the scoring rubric grows beyond 4K
tokens (e.g., expanded tech heuristics + multi-market persona overrides),
switch to a hybrid: Haiku for the initial single-token persona pass, Sonnet
for edge-case re-evaluation when Haiku returns `"unknown"` on a high-volume
account. Apply prompt caching on the static taxonomy block to minimize
Sonnet cost on the edge-case pass.

## Observability
- **Langfuse trace prefix:** `map.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `accounts_upserted` per run (target: ≥ 50 per market per week)
  - `accounts_skipped_threshold` per run — high count means account quality is degrading
  - `classify_error_rate` = `market_mapper_classify_error` log count / total candidates
  - `markets_with_zero_qualified` per run — if all target industries return 0, scraper adapters are broken
  - `pipeda_flag_count` — any non-zero count means Canadian personal data is leaking into scrape results
  - `hitl_sample_review_rate` — 10% of new accounts are flagged for `status="sample_review"`; monitor founder calibration feedback weekly

## Config Reference
All runtime config under `config/agents.yaml → market_mapper`:

| Key | Purpose |
|-----|---------|
| `target_markets` | List of target industry/geo strings fed to the scrape adapters |
| `data_sources` | Priority-ordered list of scraper adapter names |
| `min_volume_threshold` | Minimum volume_estimate to qualify an account (default: 50) |
| `min_team_size` | Minimum team_size to qualify (default: 3) |
| `persona_tier_priority` | Tier ordering for event batching priority |
