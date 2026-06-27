---
name: outcome-attribution
tier: A
agent_number: 10
graph: agents.outcome_attribution.graph:build
# NOTE: cadence is owned by scheduler._enqueue_due_attributions (config:
# agents.yaml outcome_attribution.schedule), which fans out one run per matured
# deployment. We deliberately do NOT set a `schedule:` here — the generic skill
# scheduler would crash-fire a single run with no required `deployment_id`.
triggers:
  - cron:fanout                   # daily, via scheduler._enqueue_due_attributions
  - event:deployment.health_confirmed
events_consumed:
  - deployment.health_confirmed   # from DEPLOYER (#18): {deployment_id, client_id, ...}
events_emitted:
  - attribution.report.ready      # ATTRIBUTION_REPORT_READY → strategic-arch (R3)
  - rd.insights.batch.ready       # RD_INSIGHTS_BATCH_READY — only rd_backlog feedback items
hitl: false
model_tier: SONNET                 # Claude Sonnet — summary + case-study authorship; numerics are deterministic
rate_limits:
  - anthropic
---

# PROVE — Outcome Attribution (Agent #10)

## Identity & Scope
PROVE closes the measurement loop. For every live deployment, it computes
pre/post KPI deltas, writes a founder-facing attribution summary, generates
feedback items that adjust ICP scoring weights and offer templates, and
triggers case study drafts when results cross the quality threshold. PROVE
feeds signals back to SCORE (Agent #6) and SHAPE (R3) to complete the
recursive improvement loop. PROVE does **not** modify ICP weights directly
— it emits feedback items that SCORE applies on its next run.

## Trigger & Input Contract
- **Triggers:**
  - Daily cron at 08:00 — processes all deployments with pending attribution
    (status = "live" and no existing report in window).
  - `deployment.health_confirmed` event — triggers immediately for fresh deployments.
- **Events consumed:**
  - `deployment.health_confirmed` — from DEPLOYER (Agent #18): contains `deployment_id`,
    `blueprint_id`, `status`, `task_count`, `prs`.
  - `revenue.event` — pipeline revenue and conversion data from Supabase.
- **Attribution window:** `pre_post_window_days = 30` days before and after
  deployment date.
- **Minimum delta threshold:** `min_delta_threshold = 0.10` (10%) — deltas
  below this are noted as "flat" but not highlighted as wins.

## Reasoning Chain

### Step 1 — Load KPI Data
For each deployment with `status = "live"` and no existing attribution report:
- Query `revenue_events` and `lead_conversions` for the pre-window
  (`deployment_date - 30 days → deployment_date`) and post-window
  (`deployment_date → deployment_date + 30 days`).
- Compute per-KPI delta: `(post - pre) / pre`. Round to 4 decimal places.
- Load persona KPIs from `config/agents.yaml → personas[persona].kpis` to
  determine which metrics matter for this client's persona tier.

**Persona-KPI mapping with 2026 benchmarks (what "good" looks like):**

| Persona | KPI | Baseline (industry avg) | Target (post-deploy) |
|---------|-----|------------------------|---------------------|
| ops_leader | process_cycle_time_days | 5–7 days | <2 days |
| ops_leader | manual_task_reduction_pct | 0% | 40–60% |
| revenue_leader | speed_to_lead_minutes | >60 min | <5 min |
| revenue_leader | pipeline_conversion_rate | 8% | 14%+ |
| sme_founder | owner_hours_saved_weekly | 0 hrs | 10–15 hrs |
| sme_founder | revenue_growth_rate | flat | +15–25% post-deploy |
| agency_owner | project_margin_pct | 30% | 45%+ |
| agency_owner | deliverable_cycle_days | 10–14 days | <5 days |
| saas_founder | churn_rate | 8%/mo | <4%/mo |
| saas_founder | activation_rate | 30% | 55%+ |
| hr_talent_leader | time_to_hire_days | 45 days | <25 days |
| finance_ops | close_cycle_days | 5–7 days | <3 days |

**Operator-friendly framing language (use in `summary_md` headlines):**
- ops_leader: "Your team reclaimed 12 hours per week — manual tasks down 60%"
- revenue_leader: "Lead response time dropped from 2 hours to 4 minutes post-deployment"
- sme_founder: "Owner hours saved: 14/week — reinvested into revenue-generating work"
- agency_owner: "Deliverable cycle cut from 12 days to 4 — margin up 18 points"
- saas_founder: "Activation rate up 28% in 30 days; churn dropped from 7% to 3.5%"

Do NOT use generic SaaS language ("conversion metric increased," "performance
improved"). Always frame in the business operator's own vocabulary.

### Step 2 — Attribution Summary (Claude Sonnet)
Call `SUMMARY_SYSTEM` prompt. Output is founder-facing markdown (≤180 words):
- **Headline:** one-line proof point with the single best metric movement,
  framed in business-operator language for the client's persona (e.g., "Lead
  response time down 94% in 30 days post-deployment" for a revenue_leader,
  not "conversion metric increased").
- **Wins:** bullet list of deltas ≥ `min_delta_threshold`.
- **Watch:** bullet list of regressions or stagnant metrics.
- **Confidence:** `low/med/high` based on sample sizes. Low if n < 20 events.

**Hard rule:** Do NOT invent numbers. Use only the computed deltas JSON.
If data is insufficient (post-window < 7 days), set `confidence = "low"` and
note "Attribution window is still open — revisit after {date}."

### Step 3 — Feedback Items (Claude Sonnet)
Call `FEEDBACK_SYSTEM` prompt. Output is strict JSON array (≤4 items):
```json
[{
  "target": "icp_scoring_weights|offer_templates|rd_backlog",
  "recommendation": "specific instruction",
  "rationale": "grounded in delta data",
  "confidence": 0.0–1.0
}]
```
**Feedback routing rules:**
- `icp_scoring_weights` → SCORE (Agent #6) applies on next scoring run.
- `offer_templates` → updates the offer matching templates.
- `rd_backlog` → creates an `rd_insights` row for SHAPE (R3) to pick up.

### Step 4 — Case Study Draft (conditional)
Trigger case study drafting when:
- At least one KPI delta ≥ `case_study_trigger.min_positive_delta` (0.15)
- Attribution `confidence >= case_study_trigger.min_confidence` (0.70)
- **Multi-metric triangulation required:** at least 2 KPIs must show
  positive delta before drafting. Single-metric wins may be coincidental.
  If only 1 KPI improved, write a note in `summary_md` but do NOT trigger
  case study. Log `prove_single_metric_win` for founder awareness.

**Case study conversion structure (B2B AI automation consulting sales):**
```
## Client (anonymized if flag set)
## Situation — 1 paragraph on the persona's operational pain BEFORE Omerion
## What we shipped — service_package name + demo_reference (DAAM/ORIA/RORA/ASAP)
## Results — top 3 KPI deltas with pre→post numbers in operator language
## Quote or observation — 1 line from notes field if available
## Next — 1 sentence: what the client is doing now that this is live
```
The case study must be usable as a sales asset on discovery calls. Founder
reviews before publishing — write to `case_study_drafts` with
`status = "draft"` always.

Call `CASE_STUDY_SYSTEM` prompt (≤350 words):
```
## Client (anonymized per flag)
## Situation — persona's operational pain
## What we shipped — service_package + demo_reference
## Results — top 3 KPI movements with pre→post numbers
## Quote or observation (if notes provided)
## Next — 1-sentence forward look
```
Write draft to `case_study_drafts` table with `status = "draft"` for
founder review.

### Step 5 — Write Attribution Report
Write to `attribution_reports` with:
`deployment_id`, `client_slug`, `persona`, `service_package`,
`demo_reference`, `deltas_json`, `summary_md`, `feedback_items`,
`confidence`, `window_days`, `case_study_triggered`.

**Idempotency key:** `deployment_id` — upsert on conflict.

### Step 6 — Emit
- Emit `attribution.report.ready` with `{report_id, deployment_id,
  confidence, feedback_count}`. SHAPE (R3) consumes this for weekly synthesis.
- For each feedback item with `target = "rd_backlog"`, emit `rd.backlog.item`
  with `{recommendation, rationale, confidence}`.

## Output Contract
- **Supabase table:** `attribution_reports` — one upserted row per deployment.
- **Supabase table:** `case_study_drafts` — one row when threshold met.
- **Events emitted:** `attribution.report.ready`, `rd.backlog.item` (per
  rd_backlog feedback item).

## Stop Conditions
- **Post-window < 7 days:** write report with `confidence = "low"` and note
  the open window. Do not suppress — emit normally so SHAPE has partial data.
- **Zero events in either pre or post window:** write stub report with
  `confidence = "low"`, `summary = "insufficient_data"`. Emit
  `attribution.report.ready` with `confidence = "low"`. Do not generate
  feedback items from zero data.
- **LLM returns invalid JSON for feedback items:** log
  `prove_feedback_parse_error`. Skip feedback items for this deployment.
  Do not block the attribution report write.

## Idempotency Rules
- Upsert on `deployment_id` in `attribution_reports` — running PROVE twice
  on the same deployment is safe. The second run overwrites with fresher data.
- Case study draft upsert on `(deployment_id, doc_type)` — does not create
  duplicate drafts.

## Fallback Protocol
- **Supabase KPI query fails:** log `prove_kpi_load_error`. Skip that
  deployment. Cron will retry on the next daily run.
- **LLM rate limit:** apply backoff `[4, 15, 60]` seconds. After 3 failures,
  write a stub report: `summary_md = "attribution_generation_failed"`,
  `confidence = "low"`. Log `prove_llm_rate_limit`. Still emit
  `attribution.report.ready` so SHAPE isn't blocked.
- **Anthropic API unavailable:** same as rate limit fallback above.

## Model Tier Rationale
**Claude Sonnet (summary + case study):** Attribution summaries require
persona-aware language selection (e.g., framing results differently for a
`revenue_leader` vs. a `finance_ops` leader) and case study authorship that matches
Omerion's operator-voice standard. Haiku produces generic summaries ("metrics
improved") that don't resonate with business operators. Opus is unnecessary — the
numeric attribution is fully deterministic; the LLM is only asked to
summarize and narrate, not reason. Sonnet is the correct tier for high-volume
narrative tasks with structured output constraints.

## Observability
- **Langfuse trace prefix:** `prove.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `attribution_reports_written` per week
  - `case_studies_drafted` per month — tracks evidence generation for sales collateral
  - `avg_kpi_delta` across reports — leading indicator of consulting delivery quality
  - `feedback_items_emitted` per report (target: 1–4) — feeds SCORE + SHAPE improvement loop
  - `_extract_cost()` silent fail rate — log when cost fields are absent from run results

## Config Reference
All runtime config under `config/agents.yaml → outcome_attribution`:

| Key | Purpose |
|-----|---------|
| `measurement_window_days` | Pre/post comparison window (default: 30) |
| `min_delta_threshold` | Minimum KPI delta to report (default: 0.10 = 10%) |
| `case_study_delta_threshold` | KPI delta required to trigger case study draft (default: 0.15) |
| `case_study_confidence_threshold` | Minimum attribution confidence for case study (default: 0.70) |
| `max_feedback_items` | Max feedback items per report (default: 4) |
