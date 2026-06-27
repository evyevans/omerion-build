---
name: auditor
tier: RSI
agent_number: 19
graph: agents.auditor.graph:build
triggers:
  - cron                          # nightly — cadence in config/agents.yaml (auditor.schedule), dispatched via the runtime scheduler through the full run lifecycle (create_run → execute_run), NOT skill-frontmatter direct-dispatch
  - event:healing.applied         # Immediate: any HEALER action
  - event:prompt.update.applied   # Immediate: any TRAINER prompt self-modification
events_consumed:
  - healing.applied
  - prompt.update.applied
events_emitted:
  - audit.violation.detected      # On critical constitutional violation (dedicated event; NO auto-consumer — must not re-trigger HEALER)
  - audit.sweep.complete          # On clean sweep (heartbeat)
hitl: true                        # HITL for: suspicious verdicts + weekly compliance report. Critical violations AUTO-REVERT, un-gated (constitutional design — acts then reports).
model_tier: DEFAULT               # Claude Sonnet (Tier.DEFAULT). "SONNET" is the human alias; the router enum is FAST=Haiku / DEFAULT=Sonnet / HEAVY=Opus.
rate_limits:
  - anthropic
  - supabase
owns_tables:
  - auditor_verdicts              # write — one row per audit record per run (upsert on audit_id)
  - auditor_weekly_reports        # write — one row per Monday report (insert-only)
reads_tables:
  - audit_log                     # source of truth: every self-modifying action by HEALER/TRAINER
  - founder_review_queue          # HITL-bypass verification (Rule 3)
---

# AUDITOR — Constitutional Guardian (Agent #19, RSI Department)

## Identity & Scope

AUDITOR is the **immune system** of the Omerion autonomous agency. It exists so
that no self-improvement loop — HEALER, TRAINER, or any future RSI agent — can
corrupt, compromise, or constitutionally violate the agency's operating
principles. AUDITOR trusts no other agent, including prior versions of itself.

AUDITOR verifies actions taken by peers against a **seven-rule constitution** and
reverts `critical_violation` records **immediately, without founder approval**.
This is its single most important property and its core responsibility: *the
constitution acts, then reports.* The founder is notified after a critical
revert, never asked before it.

AUDITOR runs in two modes:

| Mode | Trigger | Scope | SLA (target) |
|------|---------|-------|--------------|
| **Nightly cron** | `02:00 America/Toronto` (`auditor.schedule.cron`) | All `audited = false` records in the last `scan_window_hours` (24h) | Complete within 5 min for ≤ 500 records |
| **Event-triggered** | `healing.applied` / `prompt.update.applied` | Only the record(s) linked to `triggering_event_id` | **Time-to-verdict < 60 s** from event emit |

AUDITOR does **not** evaluate human-authored code changes (PRs are gated by
VALIDATOR + the founder). It only audits rows written by AI agents to
`audit_log`.

## The Seven Constitutional Rules — machine-checkable predicates

Each rule has a **deterministic predicate** (pure Python, runs first, no LLM) and
a **default severity**. The deterministic layer is authoritative on a hit: the
LLM verify pass may *escalate* `compliant → suspicious → critical_violation` but
may **never downgrade** a deterministic hit. Predicates below mirror
`agents/auditor/tools.py :: run_deterministic_checks`.

| # | Rule ID | Deterministic predicate (what the Python check actually tests) | Default severity | Auto-revert? |
|---|---------|----------------------------------------------------------------|------------------|--------------|
| 1 | `COST_CAP_INCREASE` | `raw_payload` has `old_<k>`/`new_<k>` for any of `per_run_cost_cap_usd`, `per_skill_daily_cost_cap_usd`, `cost_per_run_usd` where `(new-old)/old > 0.10` | `critical_violation` | ✅ revert config key to `old_<k>` |
| 2 | `UNAUTHORIZED_API` | a URL host found in `diff_summary`+`raw_payload` is not in the built-in whitelist nor `global.api_whitelist` | `critical_violation` | ✅ |
| 3 | `HITL_BYPASS` | target ends with `.skill.md` / `prompts.py` / `agents.yaml` AND (`hitl_review_id` is null OR the `founder_review_queue` row is not `decision='approved'` with non-null `decided_at`). **Fails closed** on DB error. | `critical_violation` | ✅ |
| 4 | `CORE_LOGIC_MUTATION` | `target_resource` matches a core-logic path (`omerion_core/`, any agent `graph.py`/`state.py`/`tools.py`) | `critical_violation` | ✅ via `git checkout` |
| 5 | `SECRET_EXPOSURE` | `diff_summary`+`raw_payload` matches a raw-secret regex (API key / token / PII). Partial (first-4 + redaction) is allowed in debug payloads only. | `critical_violation` | ✅ (revert + alert; secret rotation is a founder action) |
| 6 | `SCHEMA_DRIFT` | DDL keyword (`CREATE/ALTER/DROP TABLE`, `ADD/DROP COLUMN`, `CREATE/DROP INDEX`, `TRUNCATE`) present without a committed migration trail | `critical_violation` | ✅ |
| 7 | `SELF_REVERT_LOOP` | `target_resource` contains `agents/auditor`, OR `source_agent == auditor` with `action_type ∈ {revert, disable, pause, delete}` | `critical_violation` | ⛔ **never reverts AUDITOR's own files** — pages founder instead (see Revert safeguards) |

### Severity Decision Matrix

| Verdict | Meaning | Action taken this run |
|---------|---------|-----------------------|
| `compliant` | Within constitutional bounds. | Persist verdict only. No notification. |
| `suspicious` | Technically in-bounds but pattern-risky (e.g. cost creeping +9.5% three runs running; whitelisted host with an unusual path; ambiguous record). | Persist verdict → create HITL review in `founder_review_queue` → Discord suspicious notice. **No auto-revert.** |
| `critical_violation` | Directly violates ≥ 1 rule. | Persist verdict → **immediate auto-revert** → Discord critical alert → emit `audit.violation.detected`. **No HITL wait.** |

**Tie-breakers / hard rules (from `prompts.py :: VERIFY_SYSTEM`):**
- Every `audit_id` in the input must appear in the output. A missing record is treated as `suspicious`.
- When the model cannot determine compliance, it returns `suspicious`, never `compliant`. *When in doubt, flag.*
- A Rule 7 (`SELF_REVERT_LOOP`) hit is never classified below `critical_violation`.
- `verdict_reasoning` must cite the specific rule number and the exact field/value that triggered it.

## Reasoning Chain (6-Node LangGraph — `agents/auditor/graph.py`)

1. **`scan_audit_log`** — Load `audit_log` where `audited = false`, filtered by window (cron) or `triggering_event_id` (event). Cap at `max_records_per_run` (500); excess deferred to next run.
2. **`verify_guardrails`** — (2a) run all 7 deterministic checks per record; (2b) send the batch to Claude Sonnet @ `temperature=0` for semantic evaluation; (2c) merge — **deterministic hit forces `critical_violation`**; LLM may only escalate.
3. **`revert_violations`** — for each `critical_violation`, locate the record and revert. `requires_git_revert=true` → `git checkout HEAD -- <path>` (fallback `HEAD~1`); else revert the config key from `raw_payload.old_*`. Mark `audit_log.reverted=true` on success. Track `reverts_attempted/succeeded/failed`.
4. **`notify_and_persist`** — upsert every verdict to `auditor_verdicts`; critical → Discord critical alert; suspicious → `founder_review_queue` HITL + Discord notice; if zero violations + zero suspicious → clean-sweep heartbeat. Then `mark_records_audited(...)`.
5. **`generate_weekly_report`** — Mondays only (`weekly_report_day = 0`) and only in `nightly_cron` mode. Sonnet writes a ≤ 600-word Markdown compliance report over the past 7 days of `auditor_verdicts`; persisted to `auditor_weekly_reports`; posted to Discord; founder HITL ack created.
6. **`emit`** — emit the **dedicated** `audit.violation.detected` (critical found) or `audit.sweep.complete` (clean). These deliberately do **not** reuse `regression.alert` — a constitutional violation must not wake HEALER and start a heal/audit loop.

## Golden Compliance Verdict (output contract example)

A realistic 3-record nightly batch: one clean HEALER config patch, one
cost-creep pattern, one HITL bypass. This is exactly the JSON array
`verify_guardrails` produces and `notify_and_persist` consumes; each object
matches `ConstitutionalVerdict` (`agents/auditor/state.py`).

```json
[
  {
    "audit_id": "8f1c2d9a-3b44-4e21-9c77-0a1b2c3d4e5f",
    "severity": "compliant",
    "rules_violated": [],
    "revert_executed": false,
    "revert_error": null,
    "verdict_reasoning": "HEALER raised crm_nurture.backoff_seconds 30→60 via config_patch; founder_review_queue row 5d2e… is decision='approved' with decided_at set (Rule 3 satisfied). No cost-cap field changed (Rule 1 N/A). Target is agents.yaml, not core logic (Rule 4 N/A)."
  },
  {
    "audit_id": "a2b3c4d5-6e7f-4081-9a2b-3c4d5e6f7a8b",
    "severity": "suspicious",
    "rules_violated": [],
    "revert_executed": false,
    "revert_error": null,
    "verdict_reasoning": "lead_scraper_enricher per_run_cost_cap_usd rose +9.5% (1.90→2.08), the third consecutive sub-10% increase. Each step is individually within Rule 1's 10% bound, but the trend will breach the cap within two runs. Founder review recommended; no revert."
  },
  {
    "audit_id": "c3d4e5f6-7a8b-4192-ab3c-4d5e6f7a8b9c",
    "severity": "critical_violation",
    "rules_violated": ["HITL_BYPASS"],
    "revert_executed": true,
    "revert_error": null,
    "verdict_reasoning": "HEALER applied a prompt_update to agents/crm_nurture/prompts.py with hitl_review_id=null — Rule 3 requires an approved founder_review_queue row for any change to a prompts.py/.skill.md/agents.yaml surface. File reverted to HEAD; audit_log.reverted=true."
  }
]
```

**Annotated revert-failure variant** — what `revert_executed=false` looks like
on a critical (this is the trigger for the escalation loop below):

```json
{
  "audit_id": "d4e5f6a7-8b9c-41a3-bc4d-5e6f7a8b9c0d",
  "severity": "critical_violation",
  "rules_violated": ["CORE_LOGIC_MUTATION"],
  "revert_executed": false,
  "revert_error": "git checkout HEAD~1 failed: pathspec did not match (file is uncommitted)",
  "verdict_reasoning": "Detected write to omerion_core/llm/router.py (Rule 4). Auto-revert FAILED — the violating change was never committed, so git cannot restore a prior state. Violation is LIVE. Founder containment required."
}
```

## Revert safeguards (`tools.py :: execute_git_revert`)

- **Path traversal** is checked before any `git checkout` (`safe_path.startswith(_REPO_ROOT)`).
- AUDITOR **never** reverts a path containing `auditor` — Rule 7 self-protection. A Rule 7 violation is alerted + escalated, not auto-reverted.
- All subprocess calls **timeout at 30 s**. `execute_git_revert` never raises — failures are returned as `(False, message)`.
- A revert failure **never suppresses** the Discord alert or the Supabase verdict.

## Revert-Failure Escalation Loop

A critical violation whose revert fails is the most dangerous state in the
agency: the violation is live and the automatic containment did not work.

1. `revert_record` returns `revert_executed=false` with `revert_error` populated.
2. `notify_and_persist` posts the **CRITICAL** Discord alert with `revert_status: FAILED` and the `revert_error` verbatim (this is a page, not a notice).
3. A founder HITL card is created: *"AUDITOR — manual containment required: {rule} on {target} could not be auto-reverted."*
4. `emit` fires `audit.violation.detected` with `reverts_failed > 0` so the dashboard surfaces a live-violation banner.
5. **No in-run retry.** Revert failures are almost always environment issues (uncommitted file, detached HEAD, permissions) that need human diagnosis. The record stays `reverted=false`; the next sweep re-attempts (idempotent).

## Output Contract

- **`auditor_verdicts`** — one upserted row per audit record per run (upsert on `audit_id`).
- **`auditor_weekly_reports`** — one row per Monday (insert-only).
- **`audit_log`** — `audited=true` for processed records; `reverted=true` on successful revert.
- **Discord** — critical alert and/or suspicious notice and/or clean-sweep heartbeat.
- **`founder_review_queue`** — HITL rows for suspicious verdicts + the weekly report ack.
- **Events** — `audit.violation.detected` (critical found) or `audit.sweep.complete` (clean).

## Stop Conditions

| Condition | Behavior |
|-----------|----------|
| `audit_log` returns zero records | Log `auditor_no_records`; emit `audit.sweep.complete`. **Never** emit a regression alert. |
| `audit_log` read fails | Log `auditor_scan_failed`; halt gracefully. **Never** emit a false clean-sweep (silence ≠ health). |
| LLM verify fails after 3 retries | Default **all** records in the batch to `suspicious` (never `compliant`); create a HITL "AUDITOR verify failed — manual review required". |
| A revert fails | Run the Revert-Failure Escalation Loop above. Do not abort the rest of the batch. |

## Idempotency Rules

- `auditor_verdicts` upserts on `audit_id` — re-running overwrites with the fresher verdict. Safe.
- `audit_log.audited=true` is set **only after** all verdicts persist and all alerts post. A mid-run crash leaves `audited=false` → re-scanned next run. Revert idempotency is guarded by checking `audit_log.reverted` before a second revert.
- `auditor_weekly_reports` is **insert-only**; multiple Monday runs create multiple rows (dedupe at the query layer).

## Fallback Protocol

| Failure | Fallback |
|---------|----------|
| Anthropic API down during verify | ClaudeRouter retries with backoff `[4, 15, 60]`; after 3 failures default all records to `suspicious` + HITL alert. |
| Discord webhook down | Log `auditor_discord_unreachable`; continue. Delivery failure must never suppress Supabase persistence or a revert. |
| Git revert subprocess fails | Log `auditor_git_revert_failed`; set `verdict.revert_error`; run the Revert-Failure Escalation Loop. No in-run retry. |
| HITL creation fails (suspicious) | Log `auditor_hitl_create_failed`; continue. The verdict is still persisted. |
| `founder_review_queue` read fails (Rule 3) | **Fail closed** — treat as `HITL_BYPASS`. A DB error must never let an unverified self-modification stand. |

## Model Tier Rationale

**Claude Sonnet (`Tier.DEFAULT`) @ temperature 0** for verify + weekly report:
verdicts require cross-referencing structured payloads against seven rules and
emitting precise rule-cited JSON. Haiku is unreliable on Rule 3 (HITL-bypass
reasoning) and Rule 5 (secret detection in nested JSON); Opus is overkill for
structured classification. Temperature 0 makes verdicts deterministic and
auditable. The **deterministic pre-pass costs $0 LLM** and clears clean sweeps
without ever calling the model on obvious cases.

## Observability

- **Langfuse trace prefix:** `auditor.*`
- **Key metrics:**
  - `auditor_critical_violations_per_week` — any nonzero value is notable.
  - `auditor_reverts_failed` — **nonzero means a violation is still live** (page).
  - `auditor_scan_latency_ms` — should stay < 5 000 ms for a 24h window.
  - `auditor_event_time_to_verdict_ms` — should stay < 60 000 ms (event-triggered SLA).
  - `auditor_weekly_report_success` — should fire every Monday.

## Config Reference (`config/agents.yaml → auditor`)

| Key | Purpose | Default |
|-----|---------|---------|
| `schedule.cron` | Nightly sweep cadence | `0 2 * * *` (America/Toronto) |
| `scan_window_hours` | Lookback per nightly run | `24` |
| `weekly_report_day` | ISO weekday for the report (0=Mon) | `0` |
| `max_records_per_run` | Safety cap per run | `500` |
| `cost_cap_violation_threshold` | Rule 1 fractional trigger | `0.10` |
| `api_whitelist` | Approved hosts beyond the built-in set | stripe, firecrawl, hunter, serpapi, dashscope, deepseek |
| `alert_channels` | Where violations surface | `[discord, supabase]` |

## Assumptions documented during this rewrite

1. **`model_tier: DEFAULT`** is kept; "SONNET" elsewhere is a human alias for the same router tier. No churn.
2. **Severity defaults** in the rule table reflect `prompts.py` (all seven deterministic hits are `critical_violation`); `suspicious` is reserved for the LLM's pattern/ambiguity judgments, which never auto-revert.
3. **Event-triggered SLA (< 60 s)** is a target introduced by this rewrite for operator legibility; it is not yet asserted in code. Wire it as `auditor_event_time_to_verdict_ms` in `telemetry/middleware`.
