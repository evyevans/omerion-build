---
name: r3-strategic-architect
tier: R
agent_number: 13
runtime: langgraph
# SHAPE (Strategic Architect) — live path is the local LangGraph handler registered
# in agents/r3_strategic_architect/__init__.py.
triggers:
  - cron                      # PRIMARY — strategic synthesis over a 14-day window
schedule: "0 9 * * 1"         # weekly Monday 09:00 America/Toronto (after R2 at 07:00)
# R3 is a synthesizer, not a reactor: it is NOT subscribed to the per-item events
# (rd.insight.created, oss.candidate.scored) — those would re-run an Opus synthesis
# + spawn founder HITL cards on every signal. It wakes on batch/sparse events only.
events_consumed:
  - analysis.ready            # R2 per-batch heartbeat (a scout run completed)
  - dossier.ready             # SOURCE research dossier written
  - attribution.report.ready  # ATTR outcome data
events_emitted:
  - rd_proposal.submitted
hitl: true                  # HITL required — founder approves EVERY proposal before it enters the build backlog (replay-idempotent)
model_tier: OPUS            # Claude Opus — multi-source synthesis + 30/60/90 blueprint design
---

# SHAPE — Strategic Workflow Architect (Agent R3, Managed Agent)

## Identity & Scope
SHAPE is the recursive improvement engine. It synthesizes the week's R1
market signals, R2 OSS candidates, and PROVE (Agent #10) attribution reports
into 1–4 high-leverage design proposals that improve Omerion's consulting
service packages or internal OS. SHAPE does **not** execute builds (that is
RUN/Agent #9). Every proposal must clear a HITL founder review before entering
the build backlog.

## Trigger & Input Contract
- **Trigger:** Weekly cloud-managed cron (Anthropic runtime).
- **Events consumed (lookback window: 14 days, configurable via `lookback_days`):**
  - `rd.insight.created` — from TRACK (R1): market/tech signals tagged by
    service package and priority
  - `oss.candidate.scored` — from SEEK (R2): OSS candidates with fit/risk rubric
  - `attribution.report.ready` — from PROVE (Agent #10): pre/post KPI deltas
    per deployment, with confidence and feedback items
- **Minimum data to proceed:** At least one R1 insight OR one attribution
  report from the lookback window. If neither exists, halt and log
  `r3_insufficient_data`. Do not synthesize from empty context.

## Reasoning Chain

### Step 1 — Load Context
Query Supabase for:
- `rd_insights` where `run_date >= now() - 14 days`
- `rd_oss_candidates` where `run_date >= now() - 14 days` AND `fit >= 0.5`
  AND `risk < 0.7`
- `attribution_reports` where `created_at >= now() - 14 days`

Format each set as a structured block for the synthesis prompt.

### Step 2 — Synthesize (Claude Opus)
Call `SYNTHESIZE_SYSTEM` prompt with the three data blocks. Output must be
strict JSON array of 1–4 proposals:
```json
[{
  "title": "≤10 words",
  "problem_statement": "≤60 words grounded in supplied signals",
  "hypothesis": "≤40 words — the change we believe will move the KPI",
  "design_doc_md": "120–300 words: Problem / Approach / Phases / Risks",
  "target_module": "daam|capa|remi|asap|internal_os",
  "impact": "low|medium|high",
  "effort": "S|M|L|XL",
  "supporting_insight_ids": [...],
  "supporting_oss_ids": [...],
  "supporting_report_ids": [...],
  "blueprint_handoff": {
    "phase_1": "30-day MVP deliverable",
    "phase_2": "60-day expansion",
    "phase_3": "90-day measurement"
  }
}]
```

**RICE Prioritization (applied per proposal before impact/effort assignment):**
```
RICE Score = (Reach × Impact × Confidence) / Effort

Reach    = number of Omerion ICP accounts this proposal affects (1–10)
Impact   = KPI movement potential (1=minimal, 3=moderate, 5=massive uplift)
Confidence = signal strength: 1.0 (3+ corroborating signals), 0.8 (2 signals),
             0.5 (1 signal), 0.3 (hypothesis only)
Effort   = S=1, M=2, L=4, XL=8
```
RICE ≥ 10 → `impact = "high"`. RICE 5–9 → `"medium"`. RICE < 5 → `"low"`.

**General-industry KPI benchmarks (what "high impact" looks like):**
- Speed-to-lead: 30%+ reduction (industry avg >5 min; goal <60s)
- Pipeline conversion rate: 15%+ lift above baseline
- Owner hours saved: 10+ hours/week reduction in manual task time
- Process cycle time: 20%+ reduction in key workflow duration
- Client onboarding time: 30%+ reduction in days-to-first-value

**Synthesis rules:**
- `impact = "high"` reserved for proposals targeting a KPI with significant
  negative delta in attribution data OR a repeated pain signal from 3+
  R1 insights in the window, AND RICE score ≥ 10.
- Each proposal must cite at least one `supporting_*_id` from the loaded data.
- Map `target_module` to a consulting service package (canonical):
  - `daam` → `revenue_acceleration_engine` (demo: DAAM)
  - `capa` → `ops_intelligence_layer` (demo: CAPA)
  - `remi` → `research_decision_stack` (demo: REMI, Real Estate only)
  - `asap` → `process_automation_suite` (demo: ASAP)
  - `internal_os` → internal (no client package)
- Classify into one of three buckets for the founder's review queue:
  - `consulting_service_ideas` — new or improved client-facing packages
  - `icp_market_insights` — market positioning or ICP intelligence
  - `internal_os_improvements` — Omerion's own agent/infra stack
- `blueprint_handoff` must map to a real Omerion service package's
  `typical_timeline_weeks` (4/6/8/10 weeks). Phase 1 = 30 days maximum.
  Use this skeleton for every proposal's `blueprint_handoff`:
  ```
  phase_1 (30 days): Discovery + POC — baseline KPI measurement, one
    workflow automated end-to-end, founder validates output quality.
  phase_2 (60 days): MVP build + deploy — full service package implemented,
    HITL gates active, client onboarded to Discord approval flow.
  phase_3 (90 days): Optimization + handoff — attribution report generated,
    case study drafted, client self-sufficiency verified, Omerion disengages.
  ```
- **Chain-of-verification:** Before finalizing proposals, SHAPE must verify
  each `supporting_insight_id` and `supporting_oss_id` exists in Supabase
  (not hallucinated). Query `rd_insights` and `rd_oss_candidates` by ID before
  writing the proposal. If an ID is not found, remove it from supporting lists
  and log `r3_invalid_supporting_id`.
- Do NOT propose technology outside Omerion's canonical stack
  (Supabase, Pinecone, Python, LangGraph, Claude) without flagging it
  as a `deviation_note` in the proposal's `design_doc_md`.

### Step 3 — HITL Review Package
For each proposal, generate a HITL review message using `REVIEW_HEADER`:
```
**R3 Design Proposal — {title}**
Target module: `{target_module}` | Impact: `{impact}` | Effort: `{effort}` | Priority: `{priority_score}`
```
Send all proposals as a single HITL batch review task. Founder sees all
1–4 proposals in one review request and can approve, reject, or edit each
independently.

**HITL escalation timeout:** `global.hitl_escalation_minutes` (60 min). If
no response after 60 minutes, re-send alert once. After second timeout,
write proposals with `status = "pending_review"` and halt. Do not
auto-approve.

### Step 4 — Persist Approved Proposals
For each founder-approved proposal, write to `rd_proposals` with:
`title`, `problem_statement`, `hypothesis`, `design_doc_md`,
`target_module`, `target_service_package`, `target_persona`, `impact`,
`effort`, `status = "approved"`, `supporting_ids`.

### Step 5 — Emit
For each approved proposal, emit `rd_proposal.submitted` with
`{proposal_id, target_module, impact, effort}`. RUN (Agent #9) consumes
this event to trigger a build when the founder schedules it.

## Output Contract
- **Supabase table:** `rd_proposals` — one row per approved proposal with
  `status = "approved"`.
- **Event emitted:** `rd_proposal.submitted` per approved proposal.
- **HITL task created:** one batch review task per weekly run.

## Stop Conditions
- **Insufficient data (no R1 insights AND no attribution reports):** halt,
  log `r3_insufficient_data`. Do not call Opus. Do not create HITL task.
- **LLM returns <1 proposal or invalid JSON:** log `r3_synthesis_parse_error`.
  Create a HITL task with the raw output for founder review. Do not write
  to `rd_proposals` automatically.
- **Founder rejects all proposals:** log `r3_all_proposals_rejected`. Write
  rejected proposals with `status = "rejected"`. Emit nothing. Halt.
- **HITL timeout after second alert:** write proposals as `"pending_review"`,
  emit nothing, log `r3_hitl_timeout`.

## Idempotency Rules
- `rd_proposals` has no automatic upsert — each weekly synthesis creates new
  rows. Use `(title_hash, run_date)` check before inserting to prevent
  duplicate proposals if the run fires twice.
- HITL review task includes the `run_date` in the subject line to distinguish
  weekly batches.

## Fallback Protocol
- **Supabase read fails:** log `r3_supabase_read_error`. Halt the run. Do not
  synthesize from incomplete data — a partial synthesis is worse than none.
- **Opus API fails (500/503):** retry with backoff `[15, 60, 300]` seconds.
  After 3 failures, write a stub HITL task: "R3 synthesis failed — manual
  review of R1/R2/attribution data required." Log `r3_opus_failure`.
- **Pinecone unavailable:** SHAPE does not directly use Pinecone. If a
  future version adds RAG-over-insights, this fallback applies: skip RAG,
  use only the Supabase-fetched context blocks.

## Model Tier Rationale
**Claude Opus:** SHAPE performs the highest-stakes reasoning in the Omerion
OS — synthesizing heterogeneous signals (market news, OSS scores, attribution
deltas) into concrete 30/60/90-day consulting proposals with citations,
impact assessment, and canonical-stack compliance checks. Sonnet produces
proposals that are underspecified (vague `design_doc_md`) and miss subtle
signal correlations. Opus is justified by the weekly cadence (1 run/week),
low token volume (~2000–4000 tokens per synthesis), and the high business
value of each approved proposal (which can represent $25K–$60K in consulting
revenue).

**Register:** `python -m infra.anthropic.register_managed_agents r3`
**Trigger manually:** `python -m infra.anthropic.register_managed_agents --trigger r3`

## Idempotency Rules
- Supabase `rd_proposals` INSERT on each run — re-running creates duplicate proposals. Mitigate by checking for existing proposals with the same `supporting_insight_ids` before inserting.
- Chain-of-verification queries are read-only — safe to re-run.
- HITL review cards: one new card per proposal per run — not idempotent.

## Fallback Protocol
- **R1/R2/PROVE data absent for 14-day window:** SHAPE still runs but emits 0 proposals. Log `shape_no_inputs`.
- **Opus synthesis error:** log `shape_synth_failed`, proposal skipped. Run continues with remaining proposals (1–4 attempted per week).
- **RICE score below minimum threshold:** proposal downgraded to `impact="low"` and skipped from HITL batch. Log `shape_rice_below_threshold`.
- **Chain-of-verification fails (supporting ID not found):** proposal flagged with `unverified_sources` and surfaced in HITL card; founder decides whether to approve.

## Observability
- **Langfuse trace prefix:** `shape.*`
- **Key metrics to watch:**
  - `proposals_submitted` per week (target: 1–4)
  - `hitl_approval_rate` — founder approval rate per proposal
  - `avg_rice_score` per week — declining score means signal quality is degrading
  - `proposals_by_category` (consulting_service_ideas / icp_market_insights / internal_os_improvements) — distribution should reflect current business focus

## Canonical Mapping

| `target_module` | Service Package | Demo | Operator Archetype |
|---|---|---|---|
| `daam` | `revenue_acceleration_engine` | `DAAM` | `high_velocity` |
| `capa` | `ops_intelligence_layer` | `CAPA` | `system_multiplier` |
| `remi` | `research_decision_stack` | `REMI` | `capital_allocator` |
| `asap` | `process_automation_suite` | `ASAP` | `system_multiplier` |
| `internal_os` | internal | — | — |

**RICE formula:** `RICE = (Reach × Impact × Confidence) / Effort`. Reach = ICP accounts affected (1–10); Impact = KPI movement potential (1/3/5); Confidence = signal corroboration (1.0 = 3+ signals, 0.8 = 2, 0.5 = 1, 0.3 = hypothesis only); Effort = S=1, M=2, L=4, XL=8. RICE ≥ 10 → `impact="high"`.

## Golden Output — Design Proposal

One element of the 1–4 proposal array. RICE worked example: `(8 × 5 × 1.0) / 4 = 10.0` → `impact="high"`.

```json
{
  "title": "Sub-60s speed-to-lead module for DAAM",
  "problem_statement": "Three R1 insights this week show funded entrants (Acme $25M) shipping autonomous AI SDRs targeting our high_velocity ICP. PROVE attribution shows our deployed clients still average 6-minute first-touch — above the <60s benchmark.",
  "hypothesis": "Adding a webhook-triggered routing layer to the revenue_acceleration_engine will cut median speed-to-lead below 60s and lift follow-up conversion 15%+.",
  "design_doc_md": "## Problem\nFunded competitors target our high_velocity ICP; our speed-to-lead trails the <60s benchmark.\n## Approach\nVendor the MIT-licensed agent-router (R2 candidate, overall 0.81) as the dispatch layer; add a Salesforce webhook ingress to the revenue_acceleration_engine.\n## Phases\nP1 webhook ingress + routing POC; P2 full integration + HITL gating; P3 attribution + case study.\n## Risks\nSalesforce API rate limits; mitigate with backoff [4,15,60]. No canonical-stack deviation.",
  "target_module": "daam",
  "target_service_package": "revenue_acceleration_engine",
  "target_persona": "revenue_leader",
  "impact": "high",
  "effort": "M",
  "priority_score": 10.0,
  "supporting_insight_ids": ["<rd_insights.uuid-1>", "<rd_insights.uuid-2>", "<rd_insights.uuid-3>"],
  "supporting_oss_ids": ["<rd_oss_candidates.uuid>"],
  "supporting_report_ids": ["<attribution_reports.uuid>"],
  "blueprint_handoff": {
    "phase_1": "30 days: Salesforce webhook ingress + routing POC on the revenue_acceleration_engine; baseline speed-to-lead measured.",
    "phase_2": "60 days: full integration, HITL gates active, one client onboarded to the new routing flow.",
    "phase_3": "90 days: attribution report on speed-to-lead delta + conversion lift; case study drafted."
  }
}
```

**Chain-of-verification:** every `supporting_*_id` must exist in Supabase before writing the proposal. If an ID is not found, remove it from the supporting list and log `r3_invalid_supporting_id`.
