---
name: icp-scoring
tier: A
agent_number: 6
graph: agents.icp_scoring.graph:build
schedule: "0 5 * * *"             # daily 05:00
triggers:
  - cron
  - event:contact.enriched
events_consumed:
  - contact.enriched
events_emitted:
  - contact.scored
  - founder.daily_digest
hitl: false
model_tier: DEFAULT                # Sonnet for digest + intent explanations; Haiku for explanations on hot/warm; scoring is fully deterministic
rate_limits:
  - anthropic
  - openai
---

# RATE — ICP Scoring (Agent #6)

## Identity & Scope
RATE owns the daily scoring pass that turns enriched contacts into ranked prospects.
It computes Fit, Intent, and Timing sub-scores deterministically from structured CRM
data and Pinecone RAG, blends them per persona, then segments contacts into
hot / warm / watchlist / cold. LLM calls are used only for intent explanations on
hot/warm contacts and the founder daily digest — the scores themselves require no
LLM. RATE does **not** modify the `contacts` table. It does **not** send outreach.
Its output (`contact.scored` event + `scores` table) feeds REACH (Agent #4),
GROW (Agent #5), and PAIR (Agent #7).

## Trigger & Input Contract
- **Primary:** daily cron `0 5 * * *`
- **Real-time:** `contact.enriched` event pre-populates `state.candidate_contact_ids`
  so RATE scores a freshly enriched contact immediately rather than waiting for the
  next daily run.
- **Input:** `contacts` table rows updated within the last 7 days (or specific
  `candidate_contact_ids` if event-triggered), joined with `accounts`.

## Reasoning Chain (6-node LangGraph graph)

```
load
  → score              (deterministic Fit + Intent + Timing per contact)
  → persist            (upsert to scores table)
  → shortlist          (top 15 hot+warm by final score)
  → digest             (Sonnet narrative for founder)
  → emit               (CONTACT_SCORED per contact + FOUNDER_DAILY_DIGEST)
```

### Node 1 — `load`
- **Purpose:** Pull contacts eligible for scoring.
- **Tools called:** `load_candidates(contact_ids | None, since_days=7)`
- **Query:** `contacts` JOIN `accounts`, filtered to `updated_at >= 7 days ago`. When `candidate_contact_ids` set, uses `.in_()`.
- **Output:** `state.contacts` (list of raw dicts with account join)
- **Failure mode:** Supabase query error → exception propagates, run fails. Cron retries next day.

### Node 2 — `score`
- **Purpose:** Compute Fit, Intent, Timing, final score, and segment for every contact. LLM invoked only for hot/warm explanations.
- **Sub-scores (all deterministic):**
  - **Fit** = weighted sum of: `persona_tier` (tier 1→1.0, 2→0.7, 3→0.4), `deal_volume` (employee_count/50, capped 1.0), `role_seniority` (role-term match vs title), `tech_maturity` (account tier A/B/C/D), `team_size` (employee_count/30, capped 1.0)
  - **Intent** = weighted sum of: `semantic_pain_match` (Pinecone RAG cosine score over `emails` namespace, filtered by contact_id), `engagement_recency` (days since last touch, decays over 30d), `engagement_volume` (activity_log count last 30d, capped at 10)
  - **Timing** = weighted sum of: `days_since_last_touch` (decays over 14d), `stage_velocity` (stage progression speed), `deal_age` (contact age vs 90d, younger = better)
- **Final score:** per-archetype Fit/Intent/Timing blend from `agents.yaml → icp_scoring.final_weights`. Default: `{fit: 0.4, intent: 0.35, timing: 0.25}`. `system_multiplier` is more Fit-heavy (0.55/0.25/0.20) because ops automation requires strong organizational match, while `high_velocity` is more Intent-heavy (0.40/0.45/0.15).
- **Segment thresholds** (from `agents.yaml → icp_scoring.score_segments`):
  - `hot` ≥ 0.75, `warm` ≥ 0.50, `watchlist` ≥ 0.30, `cold` < 0.30
- **LLM calls:**
  - `explain_intent(router, contact, signals)` → Tier.FAST (Haiku), `max_tokens=80` — only for hot and warm contacts. Produces a ≤80-token "why now" explanation for the digest card.
- **Pinecone circuit breaker:** after 3 consecutive RAG failures, `_RagBreaker.open = True` — all remaining contacts in the batch get `semantic_pain_match = 0.0`, run continues, `rag_intent_breaker_open` logged at ERROR. Reset at each new batch start via `reset_rag_breaker()`.
- **Output:** `state.scored` (list of `ScoredContact` with fit, intent, timing, final, segment, explanations)

### Node 3 — `persist`
- **Purpose:** Write scores to Supabase.
- **Tools called:** `write_scores(run_date, scored)` → upsert on `(contact_id, run_date)`
- **Output:** `state.scored` unchanged; log `icp_scores_written` with count.
- **Idempotency:** upsert on `(contact_id, run_date)` — running RATE twice on the same day for the same contact is safe; second run overwrites with identical values.

### Node 4 — `shortlist`
- **Purpose:** Build the founder digest shortlist.
- **Logic:** sort all scored contacts by `final` desc, keep `segment ∈ {hot, warm}`, take top `max_shortlist_size` (default 15 from agents.yaml).
- **Output:** `state.shortlist`

### Node 5 — `digest`
- **Purpose:** Generate the founder-facing daily digest narrative and write to `generated_drafts`.
- **Tools called:** `render_digest(router, run_date, shortlist)` → Tier.DEFAULT (Sonnet), `max_tokens=800`
- **Output:** `state.scratch["digest_md"]`, `state.digest_sent = True`; row inserted into `generated_drafts` (`kind="daily_digest"`)
- **No HITL queue** — digest is informational only, surfaced via Sheets `Daily Digest` tab and 06:30 email.
- **Failure mode:** LLM error → `render_digest` raises; node fails. Prior node's upserted scores are already written and safe. Cron re-runs next day.

### Node 6 — `emit`
- **Purpose:** Publish events on the bus.
- **Events emitted:**
  - `CONTACT_SCORED` per contact: `{contact_id, account_id, segment, final}`
  - `FOUNDER_DAILY_DIGEST` once: `{run_date, hot_count, warm_count, shortlist_size}`

## Output Contract
- **Supabase `scores` table:** upsert on `(contact_id, run_date)` with `fit`, `intent`, `timing`, `final`, `segment`, `explanations` (JSON)
- **Supabase `generated_drafts`:** INSERT — daily digest markdown (not idempotent; re-runs create a second digest row)
- **Events emitted:** `contact.scored` per contact + `founder.daily_digest` once per run

## Guardrails
- **Never overwrite a hot/warm score with a stale run.** The upsert on `(contact_id, run_date)` means only one score per contact per day is kept.
- **Pinecone failure is non-blocking.** Circuit breaker ensures a Pinecone outage silently degrades intent scores (to 0.0) without crashing the batch. The breaker logs make the degradation visible.
- **LLM is never in the scoring critical path.** If every LLM call fails, scores are still written correctly (all deterministic). Only explanations and the digest are lost.
- **Config weights must sum to 1.0.** Malformed `fit_weights`, `intent_weights`, or `final_weights` in agents.yaml produce scores outside [0,1]. Monitor for `final > 1.0` or `final < 0.0` in the scores table.
- **Config weight KEYS must match the sub-score keys the code emits.** `compute_fit` reads `sum(sub.get(k, 0.0) * w for k, w in fit_weights.items())` — a `fit_weights` key with no matching sub-score (e.g. the old `company_size` vs the emitted `deal_volume`) silently scores **0.0**, dead-weighting that fraction of Fit with no error. Canonical Fit keys: `persona_tier`, `deal_volume`, `role_seniority`, `tech_maturity`, `team_size`. (`compute_intent`/`compute_timing` use `sub[k]` and would instead raise `KeyError` on drift — louder, but still a config-vs-code contract.)

## Stop Conditions
- **Zero contacts loaded:** all nodes after `load` produce empty results; run completes normally. Digest reads "No new signals."
- **Supabase load error:** run fails, no scores written. Cron retries next day.
- **Pinecone breaker open:** scoring continues; intent sub-score is 0.0 for all remaining contacts in batch. Log `rag_intent_breaker_open`.

## Idempotency Rules
- `scores` upsert on `(contact_id, run_date)` — re-running RATE on the same day is safe.
- `generated_drafts` INSERT is not idempotent — duplicate digests created on re-run. Low impact (informational only).

## Fallback Protocol
- **Pinecone 3 consecutive failures:** circuit breaker opens; `semantic_pain_match = 0.0` for rest of batch; run continues.
- **Haiku explain_intent call fails:** `explain_intent` returns empty string; `explanations` dict stays empty. Does not block scoring.
- **Sonnet digest call fails:** `render_digest` raises; `digest` node fails. Prior persist node has already written all scores safely.
- **agents.yaml missing persona weights:** falls back to `cfg.get("default") or {"fit": 0.4, "intent": 0.35, "timing": 0.25}`.

## Model Tier Rationale
**Scoring is fully deterministic** — no LLM in the Fit/Intent/Timing computation path.

**Haiku for `explain_intent`** — one-sentence "why now" rationale (≤80 tokens) from structured signals. Low creativity requirement. Called only for hot/warm contacts to bound cost on large batches.

**Sonnet for `render_digest`** — the daily digest is the founder's primary attention-management tool. It needs to synthesize the shortlist into a coherent, actionable narrative (not a bullet dump). Haiku would produce generic rankings; Sonnet produces business-context-aware analysis.

**Escalation rule:** For batches >500 contacts, Haiku can replace Sonnet for `explain_intent` and the quality impact is minimal. The `max_shortlist_size: 15` cap means digest complexity is bounded regardless of batch size.

## Observability
- **Langfuse trace prefix:** `rate.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `contacts_scored` per run (track growth trend)
  - `hot_count` + `warm_count` per run — pipeline health indicator
  - `rag_hit_rate` = contacts with `semantic_pain_match > 0` / total contacts
  - `rag_breaker_open_count` per week — non-zero means Pinecone reliability issue
  - `avg_score_by_archetype` — drift signals a prompt or data quality regression
  - `segment_distribution` (hot/warm/watchlist/cold ratios) — should be stable week over week

## Config Reference
All runtime config under `config/agents.yaml → icp_scoring`:

| Key | Purpose |
|-----|---------|
| `fit_weights` | Weight map for Fit sub-scores (persona_tier, deal_volume, etc.) |
| `intent_weights` | Weight map for Intent sub-scores (semantic_pain_match, etc.) |
| `timing_weights` | Weight map for Timing sub-scores (days_since_last_touch, etc.) |
| `final_weights` | Per-archetype final blend weights; `default` key required |
| `score_segments` | Threshold map: `hot`, `warm`, `watchlist` (float cutoffs) |
| `max_shortlist_size` | Max contacts in founder shortlist (default: 15) |
| `rag_query_templates` | Per-archetype semantic query strings for Pinecone intent RAG |

Also reads `config/agents.yaml → personas` (shared config) for persona tier lookup and role-term matching.
