---
name: r4-evaluation-telemetry
codename: GUARD
tier: R
agent_number: 14
runtime: managed_agent
spec: agents.r4_evaluation_telemetry.managed_agent:spec
triggers:
  - managed_agent_cron            # every 6 hours, Anthropic cloud runtime
events_consumed: []
events_emitted:
  - regression.alert              # per threshold breach — consumed by HEALER
  - proposal.ready                # heartbeat when all agents healthy (silence ≠ health)
  - deployment.degraded           # (Phase 3) client deployment health alert
  - deployment.healthy            # (Phase 3) client deployment health heartbeat
hitl: true                        # CRITICAL breaches: HITL alert + auto-pause. WARNING: log only (→ HITL at 3×).
model_tier: NONE                  # Fully deterministic — no LLM calls. All aggregation and threshold checks are pure Python.
owns_tables:
  - agent_performance_metrics     # write — upsert (agent_name, metric_date) per run
  - client_deployment_health      # (Phase 3) write — client automation health metrics
reads_tables:
  - agent_telemetry               # source of all per-agent run metrics
  - deployment_versions           # (Phase 3) active client deployments to monitor
  - cost_tracking                 # (Phase 3) cost anomaly detection
reads_config:
  - r4_evaluation_telemetry.regression_thresholds   # absolute threshold overrides
---

# GUARD — Evaluation & Telemetry (Agent R4 / `r4-evaluation-telemetry`)

> **Codename:** GUARD is the internal codename for the `r4-evaluation-telemetry`
> agent. The skill filename, `spec` path, and `agents.yaml` key use the canonical
> `r4-evaluation-telemetry` / `r4_evaluation_telemetry` names.

## Identity & Scope

GUARD is the **system health watchdog**. Every 6 hours it aggregates per-agent
telemetry from the last 14 days, compares current vs. baseline windows, and fires
regression alerts when thresholds are breached. GUARD:

- Does **not** remediate failures (HEALER's job).
- Does **not** use an LLM — all aggregation and threshold checks are pure Python.
- Watches all **15 agents**, including itself.

The event chain GUARD owns: `GUARD detects regression → emits regression.alert
→ HEALER wakes and diagnoses → HEALER formulates patch → G3 gate → apply`.

## The RSI Threshold Matrix — canonical source of truth

This table is the single authoritative gradient for the RSI department. GUARD
owns detection; HEALER owns remediation. The same table is cross-referenced from
the HEALER skill.

| Band | Metric trigger (absolute) | GUARD action | HEALER action |
|------|---------------------------|--------------|---------------|
| **Healthy** | p95 < 15 s · cost < $2.00/run · success ≥ 90% · error < 10% | Emit `proposal.ready` heartbeat. No alert. | Not woken. |
| **Warning (observe)** | 15 s ≤ p95 < 30 s · 10% ≤ error < 30% · success 80–90% | Log `r4_regression_alert` (WARNING). **3× for the same agent in one window → HITL.** Emit `regression.alert`. | Not woken by first 2 warnings; woken on 3rd-warning HITL. |
| **Actionable** | **p95 ≥ 30 s** · **error_rate ≥ 30%** · **success_rate < 90%** | Log CRITICAL. Auto-pause agent (APScheduler). HITL alert. Emit `regression.alert`. | Woken. Diagnoses + formulates bounded patch. G3 gate → apply. |
| **Founder escalation** | confidence < 0.70 after ≥ 2 HEALER attempts · loop-guard (≥ 2 recent fixes) · target outside HEALER scope | GUARD: same as Actionable. | Escalates to founder — no autonomous patch. |

**Canonical absolute thresholds** (authoritative for the RSI department):

| Metric | WARNING floor | CRITICAL / Actionable floor | Config key |
|--------|--------------|----------------------------|------------|
| `latency_p95_ms` | 15 000 ms | 30 000 ms | `r4_evaluation_telemetry.regression_thresholds.latency_p95_ms` |
| `avg_cost_usd` | $2.00/run | — (cost is warning-only) | `r4_evaluation_telemetry.regression_thresholds.cost_per_run_usd` |
| `success_rate` | < 90% (→ CRITICAL immediately) | — | `r4_evaluation_telemetry.regression_thresholds.success_rate` |
| `error_rate` | 10% | 30% | HEALER `auto_patch_thresholds.error_rate` |

> **⚠️ Implementation reconciliation required (audit finding).** The live
> detector `scripts/r4_regression_alert.py` uses **relative-delta** thresholds
> (`DEFAULT_THRESHOLDS`: `latency_p95_pct_increase` +50%/+100%,
> `failure_rate` 5%/15%) that differ from the absolute thresholds stated here.
> HEALER's `auto_patch_thresholds` and safety ceilings are currently mis-indented
> under `biz_dev_outreach:` in `agents.yaml` and read by no Python code. This
> matrix defines the *intended canonical* model; reconciling
> `r4_regression_alert.py` and the `agents.yaml` nesting is a follow-up code
> task, out of scope for this markdown-only pass.

## Trigger & Input Contract

- **Trigger:** Every 6 hours, Anthropic cloud-managed cron.
- **Input:** `agent_telemetry` Supabase table — rows with:
  `agent_name`, `status`, `duration_ms`, `cost_usd`, `tokens_input`,
  `tokens_output`, `created_at`.
- **Comparison window:** `comparison_window_days = 14` days.
  - Current window: `now - 14d` → `now`.
  - Baseline window: `now - 28d` → `now - 14d`.

## Reasoning Chain (fully deterministic — no LLM)

### Step 1 — Load Telemetry (`load_current_and_baseline`)

- Current: `created_at >= now() - 14d AND created_at < now()`
- Baseline: `created_at >= now() - 28d AND created_at < now() - 14d`
- Limit: 10 000 rows per window. If a window hits the limit, log
  `r4_telemetry_limit_hit` — data may be truncated; alert confidence is reduced.

### Step 2 — Aggregate Metrics (per agent)

For each `agent_name` in the current window compute:

| Field | Computation |
|-------|-------------|
| `run_count` | total rows |
| `success_count` | rows where `status = "success"` |
| `success_rate` | `success_count / run_count` (0.0–1.0) |
| `latency_p50_ms` | 50th percentile of `duration_ms` |
| `latency_p95_ms` | 95th percentile of `duration_ms` |
| `avg_cost_usd` | mean of `cost_usd` |
| `total_cost_usd` | sum of `cost_usd` |
| `tokens_input` / `tokens_output` | sums |

If an agent has **zero runs** in the current window, do not produce an
`AgentMetric` — log `r4_agent_no_data` with the agent name. (See Stop
Conditions for the all-agents-silent case.)

### Step 3 — Detect Regressions

Compare each current `AgentMetric` against the threshold matrix above
(overridable via `config/agents.yaml → r4_evaluation_telemetry.regression_thresholds`).

For each breach, create a `RegressionAlert`:

```
agent_name, severity, metric, current, threshold, baseline, summary
```

Example summaries:
- `"lead_scraper_enricher: p95 latency 34 200 ms ≥ 30 000 ms action floor (baseline 12 100 ms)"`
- `"crm_nurture: success_rate 0.61 < 0.90 floor (baseline 0.96) — CRITICAL, auto-pausing"`

**Zero-run agents** do not trigger threshold alerts; they trigger a separate
`r4_agent_silent` warning instead.

### Step 4 — Write Metrics

Upsert to `agent_performance_metrics` with idempotency key
`(agent_name, metric_date)`. Running GUARD 4× per day safely overwrites with
the freshest aggregate.

Fields: `agent_name`, `metric_date` (today), `runs_total`, `runs_success`,
`runs_failure`, `avg_duration_ms`, `p95_duration_ms`, `total_cost_usd`,
`window_days`, `success_rate`, `avg_cost_usd`, `tokens_input`, `tokens_output`.

### Step 5 — Triage & Escalate

**Triage decision tree (single-founder ops — no engineering team):**

```
Is success_rate < 90%?
  YES → CRITICAL
    → Auto-pause agent via APScheduler (set schedule enabled=false in DB config)
    → HITL alert to founder (Discord #founder-hitl + founder_review_queue row)
    → Alert body: subject, last 5 failure rows (created_at, duration_ms, status),
      LangSmith trace URL if LANGSMITH_PROJECT env is set
    → Emit regression.alert (severity=critical)
    → Founder must explicitly re-enable agent after investigation (no auto-resume)
  NO → Is latency_p95_ms >= 15000 OR avg_cost_usd >= 2.00 OR error_rate >= 10%?
    YES → WARNING
      → Log r4_regression_alert (WARNING)
      → Count warnings for this agent in current window
      → If warning_count < 3: log only, no HITL, emit regression.alert (severity=warning)
      → If warning_count >= 3: escalate to HITL alert (same format as CRITICAL)
    NO → HEALTHY
      → No action for this agent
```

For the HITL alert body when `LANGSMITH_PROJECT` env var is set, append:
`https://smith.langchain.com/o/{org}/projects/{LANGSMITH_PROJECT}?agent={agent_name}`
GUARD does not call LangSmith directly — it appends the URL for the founder to
click. If the env var is not set, omit the link silently.

### Step 6 — Emit

- Per `RegressionAlert`: emit `regression.alert` with
  `{agent_name, severity, metric, current, threshold, baseline, summary}`.
- If **no regressions** detected: emit `proposal.ready` heartbeat with
  `{status: "healthy", agent_count: 15, run_date: today}`.

**Silence is not health.** A missed GUARD run (no events emitted) should surface
as a staleness alert in the dashboard (check `guard_last_completed_at` in
`agent_performance_metrics` — staleness > 7 hours means GUARD is down).

## Golden Regression Alert (output contract example)

A realistic CRITICAL alert for `crm_nurture` at `success_rate = 0.61`. This
matches the `RegressionAlert` dataclass in `scripts/r4_regression_alert.py` and
the `regression.alert` event payload consumed by HEALER.

```json
{
  "agent_name": "crm_nurture",
  "severity": "critical",
  "metric": "success_rate",
  "current": 0.61,
  "threshold": 0.90,
  "baseline": 0.96,
  "delta_pct": -0.364,
  "summary": "crm_nurture: success_rate 0.61 < 0.90 floor (baseline 0.96, -36% delta) — CRITICAL, auto-pausing",
  "run_date": "2026-06-02",
  "alert_run_id": "9c6b1f20-7d3a-4e88-b1aa-2f5c9e0d4471",
  "auto_paused": true,
  "hitl_created": true,
  "langsmith_url": "https://smith.langchain.com/o/omerion/projects/omerion-agents?agent=crm_nurture"
}
```

**Annotated WARNING variant** — latency spike on `lead_scraper_enricher`, first
occurrence (log-only, no HITL, no pause):

```json
{
  "agent_name": "lead_scraper_enricher",
  "severity": "warning",
  "metric": "latency_p95_ms",
  "current": 18200,
  "threshold": 15000,
  "baseline": 9400,
  "delta_pct": 0.936,
  "summary": "lead_scraper_enricher: p95 latency 18 200 ms ≥ 15 000 ms warning floor (baseline 9 400 ms, +94% delta)",
  "run_date": "2026-06-02",
  "alert_run_id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed",
  "auto_paused": false,
  "hitl_created": false,
  "warning_count_in_window": 1
}
```

## Output Contract

- **`agent_performance_metrics`** — one upserted row per agent per day.
- **Structured log** — one `r4_regression_alert` event per breach.
- **HITL task (CRITICAL + 3× WARNING)** — one `founder_review_queue` row per
  escalating breach per run. Before creating a new HITL row, check for an
  existing unresolved row for the same `(agent_name, metric)` to avoid duplicate
  alerts.
- **Events emitted** — `regression.alert` per breach, OR `proposal.ready`
  heartbeat if all 15 agents are healthy.

## Stop Conditions

| Condition | Behavior |
|-----------|----------|
| Supabase returns zero rows for **both** windows | Log `r4_no_telemetry_data`; emit `proposal.ready` with `status: "no_data"`. **Never** emit false regression alerts. |
| **All 15 agents silent** (zero runs in current window) | Log `r4_all_agents_silent`. Create HITL alert: "GUARD: No agent runs detected in last 14 days — pipeline may be stopped." This is itself a critical signal. |
| `run_count = 0` for a specific agent | Log `r4_agent_no_data`. Do not compute metrics or fire threshold alerts for that agent. |

## Idempotency Rules

- **`agent_performance_metrics`**: upsert on `(agent_name, metric_date)` — running GUARD 4× per day is safe; each run overwrites the day's aggregate with the latest data.
- **HITL rows for CRITICAL breaches**: check `founder_review_queue` for an unresolved row on the same `(agent_name, metric)` before creating a new one. One open card per breach prevents alert fatigue.

## Fallback Protocol

| Failure | Fallback |
|---------|----------|
| `agent_telemetry` read fails | Log `r4_supabase_read_error`; halt run. APScheduler retries on next 6-hour tick. **Do not emit false healthy or false regression signals.** |
| `agent_performance_metrics` write fails | Log `r4_metrics_write_error`. Alerts **still fire** (log + HITL) — the write failure does not suppress alerting. |
| Auto-pause RPC fails | Log `r4_autopause_failed` at ERROR. Escalate to HITL immediately regardless of pause failure — the alert is the safety net. |
| HITL system unavailable | Log `r4_hitl_unavailable`. Fall back to structured log for critical alerts. Do **not** silently drop them. |
| Discord webhook down | Log `r4_discord_unreachable` at ERROR. The `regression.alert` event is still emitted on the bus. |
| GUARD breaches its own thresholds | GUARD monitors all 15 agents **including itself**. A self-breach generates an alert and auto-pause like any other agent. |

## Advanced Evaluation Rubric

Current tracked signals:

| Metric | How measured | Threshold |
|--------|-------------|-----------|
| `latency_p95_ms` | 95th percentile of `duration_ms` | WARNING ≥ 15 000 ms; CRITICAL ≥ 30 000 ms |
| `avg_cost_usd` | mean `cost_usd` per run | WARNING ≥ $2.00 |
| `success_rate` | `success` / total runs | CRITICAL < 90% |
| `error_rate` | `failed` / total runs | WARNING ≥ 10%; CRITICAL ≥ 30% |
| `agent_silent` | zero runs in current window | Always flag — pipeline may be stopped |
| `token_efficiency` | `tokens_output / tokens_input` ratio | Flag if > 3× (verbosity regression — future) |

**Coherence and tool fidelity** (future — requires LangSmith evals, outside
GUARD's deterministic scope): track via LangSmith automated eval runs. GUARD
owns telemetry; LangSmith owns quality. Keep these concerns separate.

## Model Tier Rationale

**No LLM — fully deterministic.** GUARD performs statistical aggregation (p95
percentile, success rate, cost mean) and threshold comparisons. An LLM would
introduce non-determinism into a system whose value is consistent, repeatable
measurement. Alert summaries are templated string formatting, not generation.

```bash
# Register the managed agent
python -m infra.anthropic.register_managed_agents r4

# Trigger manually for testing
python -m infra.anthropic.register_managed_agents --trigger r4
```

## Observability

GUARD **is** the observability layer. Its own health is monitored by checking:

- `guard_run_success` counter in `agent_telemetry` — should fire every 6 hours.
- `guard_last_completed_at` in `agent_performance_metrics` — staleness > 7 hours means GUARD is down.
- **Langfuse trace prefix:** `guard.*`

**Key alerts emitted:**
- `regression.alert` — per agent, per threshold breach (HEALER consumes this).
- `proposal.ready` — heartbeat when all 15 agents healthy.

## Phase 3 Extension: Client Deployment Monitoring

> **Added in Phase 3 (Enterprise Hardening).** GUARD now monitors client
> deployments in addition to internal agents.

### Client Deployment Health Check (Step 6b — new)

After completing the internal agent health check, GUARD iterates over
active client deployments from `deployment_versions` where `status = 'active'`:

1. **Health endpoint check:** `GET {preview_url}/health` for each deployment.
   - 200 OK → healthy
   - Non-200 or timeout (30s) → degraded

2. **Error rate check:** Query `cost_tracking` for the deployment.
   Count rows where `created_at > now() - interval '6 hours'`.
   If error rate > 20% → degraded.

3. **Cost anomaly check:** Compare the deployment's last 24h cost against
   its 7-day average. If current cost > 3× average → cost alert.

4. **Event emission:**
   - `deployment.degraded` with `{deployment_id, client_id, check_type,
     current_value, threshold}` → triggers HEALER for auto-remediation.
   - `deployment.healthy` → heartbeat, no action.

### Client Deployment Metrics Table

Upsert to `client_deployment_health` per deployment per check:
- `deployment_id`, `client_id`, `check_date`, `health_status`,
  `response_time_ms`, `error_rate`, `cost_24h_usd`, `cost_anomaly`.

## Assumptions documented during this rewrite

1. **Drift corrections applied:** 14 → **15 agents** (per TWATR pivot memory); "Celery beat" → **APScheduler** (per CLAUDE.md Wave-0 cutover); `hitl_tasks` / `agent_config` → **`founder_review_queue` / `agent_performance_metrics`** (live table names). No behavior change — documentation corrected to match the live architecture.
2. **Absolute threshold model** is presented as canonical (matching the task spec: p95 ≥ 30 s, error ≥ 30%, success < 90%). The live `r4_regression_alert.py` still uses relative deltas; reconciliation is flagged in the ⚠️ callout and is a follow-up code task.
3. **Duplicate sections removed:** the previous version had two copies of "Idempotency Rules" and "Fallback Protocol". Merged into single canonical sections.
4. **Phase 3 extension:** Client deployment monitoring added — GUARD now checks active client deployments for health, error rates, and cost anomalies.
