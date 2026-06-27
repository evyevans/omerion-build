---
name: compliance-checker
tier: A
agent_number: 33
graph: agents.compliance_checker.graph:build
triggers:
  - cron
  - event:client.onboarded
  - event:deployment.live
events_consumed:
  - client.onboarded
  - deployment.live
events_emitted:
  - compliance.sweep.complete
  - compliance.violation.detected
hitl: true
model_tier: DEFAULT
schedule: "0 1 * * *"
---

# COMPLIANCE_CHECKER — Business Rule Enforcement (Agent #33)

## Identity & Scope

COMPLIANCE_CHECKER runs three deterministic rule checks across the fleet nightly
and after key lifecycle events (client onboarding, new deployments). It enforces
cost caps, data retention, and API whitelist rules — all as pure Python predicates.
On Mondays, it also synthesizes a weekly trend report via Claude Sonnet.

## Rules Enforced (all deterministic Python predicates)

| Rule | Check | Logic |
|---|---|---|
| CC-1: COST_CAP | Per-run cost must not exceed agents.yaml cap | SQL lookup + float comparison |
| CC-2: DATA_RETENTION | Contact PII must not exceed 90-day retention | SQL date arithmetic |
| CC-3: API_WHITELIST | Outbound calls only to approved hosts | frozenset membership O(1) |

LLM is used ONLY for the Monday weekly trend report narrative — never for
individual rule checks.

## Graph (5 nodes)

1. `fetch_targets` — Resolve the list of target agents (deterministic)
2. `run_checks` — Execute CC-1, CC-2, CC-3 predicates (deterministic)
3. `trend_analysis` — LLM weekly trend report (ONLY on Monday)
4. `notify_persist` — Persist violations + HITL cards for critical ones
5. `emit` — Emit COMPLIANCE_VIOLATION_DETECTED / COMPLIANCE_SWEEP_COMPLETE

## W.A.R.T.T. Contract

- **W**: Nightly at 01:00 Toronto + on client.onboarded + on deployment.live
- **A**: LLM in 1 of 5 nodes (trend_analysis) — Mondays only, never decides violations
- **R**: Reads from agent_config, agent_runs, contacts, api_call_log; writes to compliance_violations
- **T**: Cron `0 1 * * *` + two event triggers
- **T**: No external MCP tools — Supabase queries only
