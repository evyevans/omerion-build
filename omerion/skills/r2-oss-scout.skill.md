---
name: r2-oss-scout
tier: R
agent_number: 12
runtime: langgraph
# SEEK (OSS Scout) — live path is the local LangGraph handler registered in
# agents/r2_oss_scout/__init__.py. Weekly cron below + reactive on rd.insight.created.
triggers:
  - cron
schedule: "0 7 * * 1"          # weekly Monday 07:00 America/Toronto
events_consumed:
  - rd.insight.created
events_emitted:
  - oss.candidate.scored
hitl: false
model_tier: HAIKU           # Claude Haiku base — rubric scoring; Sonnet for repos with risk > 0.5
---

# SEEK — OSS Scout (Agent R2, Managed Agent)

## Identity & Scope
SEEK owns the weekly open-source intelligence sweep. It searches GitHub and
package registries for AI automation-adjacent repositories, scores each against a
4-dimension rubric, and writes scored candidates for SHAPE (R3) to synthesize
into integration proposals. SEEK does **not** implement integrations (that is
RUN/Agent #9) and does **not** produce strategic proposals (that is SHAPE/R3).

## Trigger & Input Contract
- **Trigger:** Weekly cloud-managed cron (Anthropic runtime).
- **Passive event consumer:** Also runs incrementally when `rd.insight.created`
  fires with `impact_tag = "internal_os"` — treat as a supplemental signal
  to search for related OSS projects.
- **GitHub search query templates** (`config/agents.yaml → r2_oss_scout.search_tags`):
  ```
  "ai-automation" OR "workflow-automation" OR "business-automation"
  topic:langgraph OR topic:langchain OR topic:ai-agent
  "agent-orchestration" OR "rag-pipeline" OR "crm-automation"
  language:python OR language:typescript
  sort:stars, updated:<12 months
  ```
  Additional tags: LangGraph, MCP servers, RAG frameworks, outreach automation,
  email sequencing, AI SDRs, ops intelligence tools.
  Fallback if low results: search `awesome-llm-agents` or `awesome-ai-tools` curated lists on GitHub.
- **Minimum stars threshold:** 150 (filter before scoring — do not score
  repos below this threshold).
- **Activity filter:** last commit within 12 months. Repos with last commit
  >12 months are written with `maturity = 0.1` and `integration_type =
  "reference_only"` regardless of stars.

## Reasoning Chain

### Step 1 — Search (insight-seeded when triggered by R1)
When triggered by `rd.insight.created`, the R1 insight is mapped into state
(`insight_title`, `insight_impact_tag`) by `event_ingress`, and discovery is
**focused** on it — `seed_terms_from_insight` turns the title + an impact-tag
domain keyword into the GitHub query (with a couple of static tags as anchors).
So R1 flagging "LangGraph 1.0 released" steers R2 toward LangGraph repos. Cron
runs (no insight) fall back to the full static `search_tags` list.

For each query, hit the GitHub API for repositories sorted by stars. Filter to
`stars >= 150`. Collect: `name`, `repo_url`, `stars`, `language`, `license`,
`description`, `readme_excerpt` (first 3000 chars).

### Step 2 — Score (per repo)
Call `ANALYZE_SYSTEM` prompt. Output must be strict JSON:
```json
{
  "fit": 0.0–1.0,
  "maturity": 0.0–1.0,
  "composability": 0.0–1.0,
  "risk": 0.0–1.0,
  "integration_type": "component|pattern|full_module|reference_only",
  "impact_tag": "daam|capa|remi|asap|internal_os",
  "recommendation": "≤60 words"
}
```
**Scoring rules:**
- `fit` — alignment to Omerion modules (DAAM/CAPA/REMI/ASAP/internal_os).
  Score against the operator archetypes' operational pain points.
- `maturity` — stars, commit recency (last commit within 6 months = 1.0,
  > 18 months = 0.0), and evidence of production use.
- `composability` — MIT/Apache license = high; GPL/AGPL = penalize `risk`.
  Modular, extractable code scores higher than monolithic apps.
- `risk` — 0 = safe to integrate. Penalize: GPL/AGPL viral license,
  unmaintained (>18 mo), known security CVEs, deprecated dependencies.

**Hard rules:**
- Do NOT score the same `repo_url` twice in a single run.
- Flag GPL/AGPL with `risk >= 0.7` — never recommend as `component` or
  `full_module` for client-facing work.
- If `readme_excerpt` is empty, set `maturity = 0.3` and note in recommendation.

### Step 2b — High-Risk Escalation (Sonnet)
If a repo scores `risk > 0.5` after Haiku scoring, re-score with Sonnet
using the same `ANALYZE_SYSTEM` prompt. Sonnet's deeper reasoning is required
to accurately assess: dependency vulnerability chains, AGPL viral risk for
client-facing use, and security surface area in voice ISA tools.
Write the Sonnet score (overrides Haiku score) and set `scored_by = "sonnet"`
in the Supabase row. Log `r2_high_risk_escalated` with repo URL.

### Step 3 — Write to Supabase
Upsert each scored repo to `rd_oss_candidates` with all rubric fields plus
`search_tag`, `run_date`, `repo_url`.

**Idempotency key:** `repo_url` — upsert on conflict, update scores and
`run_date` on re-discovery.

### Step 4 — Emit
For each persisted candidate, emit `oss.candidate.scored` with
`{candidate_id, impact_tag, fit, risk, integration_type}`.
SHAPE (R3) consumes this event for weekly synthesis.

## Output Contract
- **Supabase table:** `rd_oss_candidates` — upserted rows per repo.
- **Event emitted:** `oss.candidate.scored` per persisted candidate.

## Stop Conditions
- **Search returns zero repos for all tags:** complete run, log
  `r2_zero_repos_found`. Emit nothing.
- **All repos below `min_stars` threshold:** same as above.
- **LLM returns invalid JSON:** log `r2_score_parse_error` with repo URL.
  Skip that repo. Do not block the run.
- **Repo has GPL/AGPL license and `fit >= 0.7`:** write to Supabase with
  `risk = 0.9` and `integration_type = "reference_only"`. Include a note in
  `recommendation` flagging the license conflict. Do not suppress — let
  SHAPE decide.

## Idempotency Rules
- Upsert on `repo_url` — running SEEK twice in one week is safe.
- `run_date` is updated on every upsert; use it to filter stale candidates
  in SHAPE's synthesis window.

## Fallback Protocol
- **GitHub API rate limit (403/429):** apply exponential backoff
  `[4, 15, 60]` seconds. After 3 retries, log `r2_github_rate_limit` with
  the tag that failed. Skip that tag, continue to remaining tags.
- **Supabase write fails:** log `r2_supabase_write_error`. Skip that repo.
  Cloud runtime retries on next weekly cycle via upsert idempotency.
- **LLM rate limit:** same backoff pattern. After 3 failures on one repo,
  write a stub row with `fit = null`, `recommendation = "scoring_failed"`.

## Model Tier Rationale
**Claude Haiku base, Sonnet for high-risk (corrected — Grok-validated):**
The majority of repos are straightforward to score — standard MIT/Apache
library with clear README, obvious fit/composability signals. Haiku handles
these accurately at the structured JSON rubric level. The cost savings at
20–40 repos/week are meaningful.

**Sonnet escalation** applies only when `risk > 0.5` (after Haiku pass).
High-risk repos require deeper reasoning: GPL/AGPL viral chain analysis,
unmaintained dependency trees, security surface area in voice tools. Sonnet
on ~5–10% of repos per run is the correct hybrid. Total weekly LLM cost
stays well below the cost of a flat-Sonnet approach.

**Register:** `python -m infra.anthropic.register_managed_agents r2`
**Trigger manually:** `python -m infra.anthropic.register_managed_agents --trigger r2`

## Idempotency Rules
- Supabase `rd_oss_candidates` upsert on `repo_url` — re-scoring the same repo is safe; scores are overwritten.
- GitHub API calls are read-only — always safe to re-run.
- Pinecone namespace not used by R2 — no embed idempotency concern.

## Fallback Protocol
- **GitHub API rate limit (403):** back off per `X-RateLimit-Reset` header, retry. After 3 failures, skip that repo, log `r2_github_rate_limit`, continue with remaining batch.
- **Haiku scoring error:** repo skipped, logged as `r2_score_failed`. Re-run on next weekly cycle.
- **Sonnet escalation error (risk > 0.5):** fall back to Haiku score result, log `r2_sonnet_escalation_failed`. Do not block insertion.
- **Supabase write error:** log `r2_upsert_failed`, repo skipped.

## Observability
- **Langfuse trace prefix:** `r2.*`
- **Key metrics to watch:**
  - `repos_scored` per week
  - `high_fit_candidates` (fit ≥ 0.7) per week — pipeline health for R3 synthesis
  - `sonnet_escalation_rate` = repos with `risk > 0.5` / total scored — should be 5–15%
  - `gpl_flagged_count` — any non-zero needs review before integration recommendation

## Canonical Mapping

| `impact_tag` | Service Package | Demo | Operator Archetype |
|---|---|---|---|
| `daam` | `revenue_acceleration_engine` | `DAAM` | `high_velocity` |
| `capa` | `ops_intelligence_layer` | `CAPA` | `system_multiplier` |
| `remi` | `research_decision_stack` | `REMI` | `capital_allocator` |
| `asap` | `process_automation_suite` | `ASAP` | `system_multiplier` |
| `internal_os` | internal | — | — |

**Rubric definitions:**
- `fit` — 0–1 alignment to a service package or internal_os
- `maturity` — 0–1 (last commit <6 months = 1.0; >18 months = 0.0)
- `composability` — 0–1 (MIT/Apache = high; GPL/AGPL = low)
- `risk` — 0 safe → 1 avoid (GPL/AGPL: `risk ≥ 0.7` + `integration_type = "reference_only"`)
- `overall` — computed in `tools.py` as `(fit + maturity + composability) / 3 × (1 - risk)`
- Sonnet escalation: **`risk > 0.5` after Haiku pass** → re-score with Sonnet

## Golden Output — Repository Tear-Down

Two examples: one clean MIT (Haiku-scored), one AGPL high-risk (Sonnet-escalated).

```json
{
  "repo_url": "https://github.com/example/agent-router",
  "name": "agent-router",
  "stars": 4200,
  "language": "Python",
  "license": "MIT",
  "last_commit": "2026-05-18",
  "search_tag": "agent-orchestration",
  "fit": 0.82,
  "maturity": 0.90,
  "composability": 0.88,
  "risk": 0.15,
  "overall": 0.81,
  "integration_type": "component",
  "impact_tag": "internal_os",
  "service_package_tag": "internal",
  "scored_by": "haiku",
  "recommendation": "MIT-licensed, actively maintained, modular router for multi-agent tool dispatch. Vendor the dispatch layer into Omerion's internal_os to replace bespoke routing in build_orchestrator. Low risk; no client-facing exposure.",
  "run_date": "2026-06-03"
}
```

```json
{
  "repo_url": "https://github.com/example/crm-sync-agpl",
  "name": "crm-sync-agpl",
  "stars": 980,
  "language": "TypeScript",
  "license": "AGPL-3.0",
  "last_commit": "2024-09-02",
  "search_tag": "crm-automation",
  "fit": 0.74,
  "maturity": 0.30,
  "composability": 0.20,
  "risk": 0.90,
  "overall": 0.41,
  "integration_type": "reference_only",
  "impact_tag": "daam",
  "service_package_tag": "revenue_acceleration_engine",
  "scored_by": "sonnet",
  "recommendation": "High fit for CRM sync but AGPL-3.0 is viral and unsafe for client-facing delivery; last commit >18mo ago. Study the sync pattern only — do NOT vendor. Escalated to Sonnet due to risk>0.5 (license + abandonment).",
  "run_date": "2026-06-03"
}
```
