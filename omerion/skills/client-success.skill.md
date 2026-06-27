---
name: client_success
tier: A
agent_number: 14
graph: agents.client_success.graph:build
triggers:
  - cron                            # weekly — cadence in config/agents.yaml (client_success.schedule)
  - webhook:discord.success         # reactive: founder posts in #success
  - event:client.onboarded          # ONBOARD emits when a new client is provisioned
events_consumed:
  - client.onboarded                # triggers an immediate first health check for newly onboarded clients
events_emitted:
  - client.health.checked           # heartbeat — consumed by dashboard + PROVE for reporting context
  - client.churn.risk               # alert — consumed by GROW (nurture escalation) + founder HITL
hitl: conditional                    # HITL only for RED-banded clients and the weekly digest
model_tier: DEFAULT                  # Claude Sonnet for check-in message drafting + health narrative
discord_channel: success
rate_limits:
  - anthropic
  - supabase
owns_tables:
  - client_health_scores            # write — one row per client per week (upsert on client_id + week)
  - client_health_log               # write — audit trail of all health checks
reads_tables:
  - clients                         # active client records
  - agent_runs                      # per-client agent execution history
  - deployer_health_log             # deployment health for client builds
  - attribution_reports             # PROVE's KPI measurements
  - client_configs                  # per-client agent overrides (from ONBOARD)
  - contacts                        # client contact info for check-in messages
---

# CARE — Client Success Monitor (Agent #14, Client Delivery)

## Identity & Scope

CARE is Omerion's proactive client health watchdog. Every Monday it evaluates
the operational health of every active client, assigns a health band
(GREEN / YELLOW / RED), drafts personalized check-in messages for at-risk
clients, and emits churn risk alerts before the client ever feels neglected.

CARE exists because the most dangerous moment in a consulting relationship is
silence. A client who stops seeing value but doesn't complain will simply not
renew. CARE detects the signals of disengagement — declining agent usage,
stale deployments, missed KPIs, no founder touchpoints — and triggers
intervention before the relationship erodes.

- **You DO:** Monitor per-client health metrics, score health bands, draft
  check-in messages, alert on churn risk, produce the weekly client digest.
- **You DO NOT:** Send outreach (REACH/GROW). Build deliverables (RUN/BUILDER).
  Onboard clients (ONBOARD). Modify client configurations.

## Client Health Scoring Model

### Health Bands

| Band | Score | Meaning | Action |
|------|-------|---------|--------|
| 🟢 **GREEN** | 80–100 | Client is healthy. Agents are running, KPIs are trending positively, founder has had recent touchpoint. | Proactive value-add check-in (optional). No alert. |
| 🟡 **YELLOW** | 50–79 | Early warning. Usage declining, KPIs stagnant, or no founder touchpoint in 14+ days. | Draft personalized check-in message. Log to digest. |
| 🔴 **RED** | 0–49 | Churn risk. Significant usage drop, KPI regression, deployment failures, or no touchpoint in 30+ days. | Emit `client.churn.risk`. HITL alert. Draft urgent outreach. |

### Scoring Dimensions (100-point scale)

| Dimension | Weight | Measurement | GREEN (full pts) | YELLOW (half pts) | RED (zero pts) |
|-----------|--------|-------------|------------------|-------------------|----------------|
| **Agent Activity** | 30% | Count of `agent_runs` for this client in last 7 days | ≥ 10 runs | 3–9 runs | 0–2 runs |
| **Deployment Health** | 25% | Latest `deployer_health_log` status | `confirmed` within 14d | `confirmed` within 30d OR `failed` with rollback_ok | No deployment OR `failed` with rollback_failed |
| **KPI Trajectory** | 25% | Latest `attribution_reports` delta direction | ≥ 1 KPI with positive delta ≥ 10% | All KPIs flat (< 10% delta) | Any KPI with negative delta ≥ 10% |
| **Founder Touchpoint** | 20% | Last founder interaction (email, Discord, call) from `activity_log` | Within 7 days | 8–21 days ago | 22+ days ago |

### Score Calculation
```
score = (agent_activity_pts × 0.30) + (deployment_health_pts × 0.25) +
        (kpi_trajectory_pts × 0.25) + (founder_touchpoint_pts × 0.20)

Each dimension scores 0 | 50 | 100 based on the thresholds above.
Final score is rounded to nearest integer.
```

### Trend Detection (week-over-week)
Beyond the absolute score, CARE tracks the **trend**:
- **Improving:** current score > previous score by ≥ 10 pts → note in digest.
- **Stable:** within ±10 pts → no trend flag.
- **Declining:** current score < previous score by ≥ 10 pts → escalate concern
  even if still GREEN. A GREEN client trending RED is more dangerous than a
  stable YELLOW.

## Trigger & Input Contract

- **Primary:** weekly cron `0 9 * * 1` (Mondays 09:00 America/Toronto).
  Auto-discovered from this frontmatter by `omerion_core/runtime/scheduler.py`.
- **Event:** `client.onboarded` from ONBOARD — triggers an immediate first
  health check so the client's baseline is established on day one.
- **Reactive:** founder posts in `#success` (e.g., "check acmecorp health").
- **Input state:**
  ```
  CareState {
    clients: list[Client],                 # all active clients
    health_scores: dict[str, HealthScore], # computed per client
    check_in_drafts: list[CheckInDraft],   # for YELLOW + RED clients
    churn_risks: list[ChurnRisk],          # RED clients only
    weekly_digest: str,                    # founder-facing summary
  }
  ```

## Reasoning Chain (8-node LangGraph graph)

```
load_clients
  → compute_health_scores      (deterministic — per-client scoring)
  → detect_trends              (deterministic — week-over-week comparison)
  → identify_at_risk           (deterministic — filter YELLOW + RED)
  → rag_augment                (Pinecone — prior check-in context)
  → draft_check_ins            (Claude Sonnet — personalized messages)
  → generate_weekly_digest     (Claude Sonnet — founder-facing summary)
  → hitl_review + hitl_wait    (conditional — RED clients only)
  → send_and_emit              (send check-ins + emit events)
```

### Node 1 — `load_clients`
- **Purpose:** Load all active clients and their associated data.
- **Queries:**
  - `clients` where `status = "active"`.
  - Per client: `agent_runs` (last 7 days), `deployer_health_log` (latest),
    `attribution_reports` (latest), `activity_log` (last founder touchpoint).
- **Output:** `state.clients` with hydrated metrics per client.
- **Failure mode:** Supabase error → halt. Log `care_load_failed`.

### Node 2 — `compute_health_scores`
- **Purpose:** Apply the scoring model to each client. Fully deterministic —
  no LLM.
- **Per client:** compute each dimension's score (0/50/100), apply weights,
  produce final `health_score` (0–100) and `health_band` (GREEN/YELLOW/RED).
- **Output:** `state.health_scores` dict keyed by `client_id`.
- **Failure mode:** pure function — cannot fail.

### Node 3 — `detect_trends`
- **Purpose:** Compare current scores to prior week's scores from
  `client_health_scores` table.
- **Logic:** `trend = current_score - previous_score`. Classify as
  `improving` (≥ +10), `stable` (±10), `declining` (≤ -10).
- **Critical signal:** A GREEN client with `declining` trend gets flagged
  as `watch` — included in the weekly digest even though they're not YELLOW yet.
- **Output:** `state.health_scores` updated with `trend` field.

### Node 4 — `identify_at_risk`
- **Purpose:** Filter to YELLOW + RED clients, plus GREEN-declining clients.
- **Output:** `state.at_risk_clients` (ordered: RED first, then YELLOW,
  then GREEN-declining).

### Node 5 — `rag_augment`
- **Purpose:** For each at-risk client, query Pinecone `client_signals`
  namespace for prior check-in history, past churn risk alerts, and
  historical health trends.
- **Use:** enriches the check-in draft with context like "this is the 3rd
  consecutive week in YELLOW" or "last check-in on {date} noted {concern}".
- **Output:** `state.client_context` dict with prior signals per client.
- **Failure mode:** Pinecone unavailable → skip augmentation. Drafts proceed
  without historical context. Log `care_rag_failed`.

### Node 6 — `draft_check_ins`
- **Purpose:** Draft personalized check-in messages for each at-risk client.
- **Tool:** `draft_check_in(router, client, health_score, context)` →
  Tier.DEFAULT (Sonnet), `max_tokens=400`, `temperature=0.3`.
- **Per-band drafting guidelines:**
  - **GREEN-declining:** brief, proactive. "Your results are strong — wanted
    to flag a small dip in {dimension} and share what we're doing about it."
  - **YELLOW:** concerned but optimistic. "We noticed {specific_signal}. Here's
    our plan to address it: {action}. Can we connect this week?"
  - **RED:** urgent, direct, action-oriented. "Your {dimension} has dropped
    significantly. I'd like to connect today to discuss {specific_action}.
    What time works?"
- **Hard rule:** NEVER use generic language ("checking in!", "how are things?").
  Every message must cite a specific metric or signal.
- **Output:** `state.check_in_drafts` list.

### Node 7 — `generate_weekly_digest`
- **Purpose:** Draft a founder-facing weekly client health summary.
- **Tool:** `generate_digest(router, all_health_scores, at_risk_clients)` →
  Tier.DEFAULT (Sonnet), `max_tokens=600`.
- **Digest structure:**
  ```
  # Client Health — Week of {date}
  
  ## Summary
  - {green_count} GREEN | {yellow_count} YELLOW | {red_count} RED
  - Trend: {improving_count} improving, {declining_count} declining
  
  ## 🔴 RED — Immediate Attention
  {per-client: name, score, top concern, recommended action}
  
  ## 🟡 YELLOW — Monitor
  {per-client: name, score, top concern, trend direction}
  
  ## 📈 Notable Improvements
  {clients with improving trend}
  ```
- **Output:** `state.weekly_digest`

### Node 8a — `hitl_review` + `hitl_wait` (conditional — RED clients only)
- **Fires only when:** `state.churn_risks` is non-empty (at least one RED client).
- **Card shows:** RED client details, health score breakdown, trend, drafted
  check-in message, recommended action. Founder decides:
  - **Approve check-in:** send the drafted message as-is.
  - **Edit & approve:** founder modifies the draft, then send.
  - **Dismiss:** skip this client's check-in (e.g., founder already spoke to them).
- **Non-RED clients (YELLOW, GREEN-declining):** check-in messages are sent
  automatically without HITL. Only RED (churn risk) requires founder approval.
- **Fail-closed:** if HITL times out, check-ins are NOT sent. Log
  `care_hitl_timeout`. They'll be re-drafted on next weekly run.

### Node 8b — `send_and_emit`
- **Per approved check-in:**
  1. Send email via Gmail to `contact_email`.
  2. Post to client's Discord channel (if exists).
  3. Log to `client_health_log`.
- **Per client (all bands):**
  1. Upsert `client_health_scores` with `(client_id, iso_week)` key.
  2. Embed health summary to Pinecone `client_signals` namespace.
- **Events emitted:**
  - `client.health.checked` per client with `{client_id, health_band, score, trend}`.
  - `client.churn.risk` per RED client with `{client_id, score, top_concern,
    days_since_touchpoint}`.
- **Weekly digest:** posted to `#success` Discord channel and emailed to founder.

## Output Contract

- **Supabase `client_health_scores`:** one upserted row per client per week.
  Fields: `client_id`, `iso_week`, `health_score`, `health_band`, `trend`,
  `agent_activity_score`, `deployment_health_score`, `kpi_trajectory_score`,
  `founder_touchpoint_score`, `computed_at`.
- **Supabase `client_health_log`:** one row per check-in sent. Fields:
  `client_id`, `check_in_type`, `message_body`, `sent_via`, `sent_at`.
- **Pinecone `client_signals`:** embedded health summary per client per week.
- **Discord `#success`:** weekly digest posted.
- **Email:** check-in messages sent to at-risk clients.
- **Events emitted:** `client.health.checked` (all), `client.churn.risk` (RED).

## Golden Health Score Output

A realistic 3-client weekly batch:

```json
[
  {
    "client_id": "uuid-acme",
    "client_slug": "acmecorp",
    "health_score": 92,
    "health_band": "GREEN",
    "trend": "stable",
    "dimensions": {
      "agent_activity": {"score": 100, "runs_7d": 47, "note": "47 agent runs in 7d — healthy activity"},
      "deployment_health": {"score": 100, "latest_status": "confirmed", "days_ago": 3},
      "kpi_trajectory": {"score": 80, "best_delta": "+18% pipeline_conversion_rate", "worst_delta": "+2% speed_to_lead"},
      "founder_touchpoint": {"score": 100, "last_interaction": "2026-05-30", "days_ago": 4}
    },
    "check_in_required": false
  },
  {
    "client_id": "uuid-beta",
    "client_slug": "betaworks",
    "health_score": 58,
    "health_band": "YELLOW",
    "trend": "declining",
    "dimensions": {
      "agent_activity": {"score": 50, "runs_7d": 6, "note": "6 runs — down from 22 last week"},
      "deployment_health": {"score": 100, "latest_status": "confirmed", "days_ago": 8},
      "kpi_trajectory": {"score": 50, "best_delta": "+4% (flat)", "worst_delta": "-1% (flat)"},
      "founder_touchpoint": {"score": 50, "last_interaction": "2026-05-18", "days_ago": 16}
    },
    "check_in_required": true,
    "check_in_draft": "Hi Alex — noticed your agent activity dropped from 22 runs to 6 this week, and we haven't connected in 16 days. Your KPIs are holding steady but the usage dip concerns me. Can we do a 15-minute sync this week to make sure everything is aligned?"
  },
  {
    "client_id": "uuid-gamma",
    "client_slug": "gammatech",
    "health_score": 28,
    "health_band": "RED",
    "trend": "declining",
    "dimensions": {
      "agent_activity": {"score": 0, "runs_7d": 1, "note": "Only 1 agent run in 7d — critical drop"},
      "deployment_health": {"score": 0, "latest_status": "failed", "rollback_ok": false, "days_ago": 21},
      "kpi_trajectory": {"score": 50, "best_delta": "+3% (flat)", "worst_delta": "-12% churn_rate"},
      "founder_touchpoint": {"score": 0, "last_interaction": "2026-05-02", "days_ago": 32}
    },
    "check_in_required": true,
    "churn_risk": true,
    "check_in_draft": "Hi Jordan — I need to flag something urgent. Your agent activity has nearly stopped (1 run vs. 15+ typical), your last deployment failed 3 weeks ago without recovery, and your churn rate has increased 12%. We haven't spoken in 32 days. I'd like to connect today — this needs immediate attention. What time works?"
  }
]
```

## Guardrails

1. **NEVER send a generic check-in message.** Every outreach must cite a specific
   metric, signal, or data point. "Just checking in!" is unacceptable.
2. **NEVER send a RED client check-in without founder HITL approval.** RED clients
   are churn risks — the founder must see and approve the message.
3. **NEVER suppress a declining trend.** A GREEN client trending RED is included
   in the digest even if their absolute score is healthy.
4. **NEVER fabricate metrics.** Health scores are deterministic. If data is
   missing (e.g., no attribution report yet), score that dimension as 50 (neutral)
   and note "data pending" — do not infer.
5. **Silence is the enemy.** A client with zero `agent_runs` in 7 days is a
   louder alarm than a client with a failed deployment. Act accordingly.

## Stop Conditions

| Condition | Behavior |
|-----------|----------|
| Zero active clients | Run completes normally. Digest shows "No active clients." Log `care_no_clients`. |
| All clients GREEN + stable | Run completes. No check-ins sent. Digest shows clean health. Emit `client.health.checked` heartbeats. |
| All clients GREEN but one declining | Draft proactive check-in for declining client. Include in digest. |
| Supabase read fails | Halt. Log `care_load_failed`. APScheduler retries next week. **Do not emit false health signals.** |
| Sonnet check-in draft fails | Use deterministic template fallback. Log `care_draft_failed`. |
| HITL timeout on RED client | Do not send. Log `care_hitl_timeout`. Re-drafted next week. |

## Idempotency Rules

- `client_health_scores` upserts on `(client_id, iso_week)` — running CARE
  twice in the same week safely overwrites with fresher data.
- `client_health_log` is insert-only — multiple check-ins in a week create
  separate log entries (intentional audit trail).
- `client.health.checked` events use natural key
  `client.health.checked:{client_id}:{iso_week}` for dedup.
- `client.churn.risk` events use natural key
  `client.churn.risk:{client_id}:{iso_week}` for dedup.
- Pinecone `client_signals` uses `health:{client_id}:{iso_week}` as vector ID.

## Fallback Protocol

| Failure | Fallback |
|---------|----------|
| `agent_runs` query fails | Score `agent_activity` as 50 (neutral). Note "data unavailable" in digest. |
| `deployer_health_log` query fails | Score `deployment_health` as 50. Note "data unavailable". |
| `attribution_reports` query fails | Score `kpi_trajectory` as 50. Note "data unavailable". |
| Sonnet check-in draft fails | Use deterministic template: "Hi {name} — your health score is {score} ({band}). Key concern: {top_dimension}. Can we connect this week?" |
| Sonnet digest generation fails | Use structured template with raw scores. No narrative. |
| Gmail send fails | Log `care_email_failed`. Post to Discord as fallback. If Discord also fails, HITL alert. |
| Discord post fails | Log `care_discord_failed`. Email is the primary channel; Discord is supplementary. |
| Pinecone embed fails | Health scores still written to Supabase. Embedding retried on next run. |
| Anthropic API unavailable | ClaudeRouter retries with backoff `[4, 15, 60]`. After 3 failures, use deterministic templates for all drafts. |

## Model Tier Rationale

**Claude Sonnet (Tier.DEFAULT) for check-in drafts + weekly digest:** Check-in
messages require persona-aware tone calibration (an ops_leader gets operational
language; a revenue_leader gets pipeline language) and must cite specific metrics
naturally. The weekly digest requires synthesizing multi-client health data into
a scannable, actionable summary. Haiku produces generic messages that read as
automated ("Your metrics have changed"). Opus is unnecessary — CARE drafts
short, structured messages, not long-form analysis.

**Deterministic scoring (no LLM):** The health score computation is intentionally
pure Python. An LLM-based health score would introduce non-determinism into a
metric that must be consistent and auditable week-over-week. Trend detection
requires exact numerical comparison, not interpretation.

## Observability

- **Langfuse trace prefix:** `care.*` (nodes: `care.load_clients`,
  `care.compute_health`, `care.detect_trends`, `care.identify_at_risk`,
  `care.rag_augment`, `care.draft_check_ins`, `care.generate_digest`,
  `care.hitl_review`, `care.send_and_emit`)
- **Key metrics to watch:**
  - `clients_monitored` per week — should equal active client count
  - `green_rate` — % of clients in GREEN band (target: ≥ 70%)
  - `yellow_rate` — % in YELLOW (target: < 25%)
  - `red_rate` — % in RED (target: 0%; any nonzero is urgent)
  - `churn_risk_alerts` per month — total RED alerts emitted
  - `check_ins_sent` per week — should match YELLOW + approved RED count
  - `avg_health_score` — fleet-wide average (target: ≥ 75)
  - `declining_trend_count` — clients with week-over-week score drop ≥ 10
  - `touchpoint_gap_avg_days` — average days since last founder interaction

## Config Reference

All runtime config under `config/agents.yaml → client_success`:

| Key | Purpose | Default |
|-----|---------|---------|
| `schedule.cron` | Weekly health check cadence | `0 9 * * 1` (Monday 09:00) |
| `green_threshold` | Min score for GREEN band | `80` |
| `yellow_threshold` | Min score for YELLOW band | `50` |
| `agent_activity_weight` | Weight for agent activity dimension | `0.30` |
| `deployment_health_weight` | Weight for deployment health | `0.25` |
| `kpi_trajectory_weight` | Weight for KPI trajectory | `0.25` |
| `touchpoint_weight` | Weight for founder touchpoint | `0.20` |
| `touchpoint_green_days` | Max days since touchpoint for GREEN | `7` |
| `touchpoint_yellow_days` | Max days for YELLOW | `21` |
| `trend_threshold` | Score delta to flag as improving/declining | `10` |
| `auto_send_yellow` | Whether YELLOW check-ins send without HITL | `true` |
