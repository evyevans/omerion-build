# Auditor Constitutional Rules

Last updated: 2026-06-04
Maintained by: GUARD (auditor, Agent #19)

The auditor enforces 7 rules against every record in audit_log.
Rules 1–4 are deterministic (no LLM required). Rules 5–7 require LLM semantic evaluation.
Auto-revert fires immediately for CRITICAL. SUSPICIOUS routes to weekly report only.

## Rule Reference

| # | Rule | Trigger | Severity | Response |
|---|------|---------|----------|---------|
| 1 | No self-approval | source_agent == approved_by | CRITICAL | Auto-revert + Discord alert |
| 2 | No prod write without backup | type in [schema_change, config_patch] AND backup_ref IS NULL | CRITICAL | Auto-revert |
| 3 | HITL bypass guard | type == prompt_update AND hitl_review_id IS NULL | CRITICAL | Auto-revert |
| 4 | Cost ceiling | cost_usd > agent_config.cost_ceiling_usd | CRITICAL | Auto-revert + freeze agent |
| 5 | Schema drift | Column rename/type widen/constraint drop without migration | SUSPICIOUS | Weekly report flag |
| 6 | Cost creep | 3+ consecutive runs > 80% of ceiling (not yet over) | SUSPICIOUS | Weekly report flag |
| 7 | Loop guard | healer or trainer fixed same agent ≥2× in 7 days | SUSPICIOUS | Escalate to founder |

## Auto-Revert Protocol

1. Read `audit_log.backup_ref` from the offending record.
2. File path → restore via `omerion_core.backup.restore()`.
3. Migration ID → execute rollback SQL.
4. Set `audit_log.reverted = true`, `auditor_verdicts.revert_status = "completed"`.
5. Emit `audit.revert.completed` to Discord #mission-control.

If backup_ref IS NULL and revert required: escalate. NEVER attempt blind revert.

## Severity Verdicts

- **CRITICAL** — Any of Rules 1–4 match. Deterministic. LLM not required for verdict.
- **SUSPICIOUS** — Rules 5–7. LLM output must be exactly `suspicious` or `clean`. No other tokens.
- **CLEAN** — No rule triggered; LLM found no anomalies.

Rule 3 exception: `hitl_review_id` may be absent on read-only actions (type in `[read_telemetry, read_config]`).
Only applies to write actions (type in `[prompt_update, config_patch, schema_change, file_write]`).
