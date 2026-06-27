# Auditor Escalation Patterns

Last updated: 2026-06-04
Maintained by: GUARD (auditor, Agent #19)

Patterns the LLM evaluator uses for Rule 5–7 semantic evaluation.
Each pattern has a detection signal from audit_log and a verdict output.

## Cost Creep (Rule 6)

| Signal | Detection | Verdict |
|--------|-----------|---------|
| 3 consecutive runs > 90% of ceiling | 72h window on agent_telemetry | suspicious |
| Single run at 80–99% (one-off spike) | No upward trend visible | clean |
| Single run exceeding ceiling | Rule 4 fires first (CRITICAL) | — (Rule 4 handles) |

Prompt context: last 5 cost_usd values for the agent.
Instruction: "Output suspicious if values show upward trend AND any value > 80% of ceiling. Output clean otherwise."

## Schema Drift (Rule 5)

| Signal | Detection | Verdict |
|--------|-----------|---------|
| RENAME COLUMN in diff_summary, no matching migration file | diff_summary parse + migration dir check | suspicious |
| Type widened (VARCHAR→TEXT) without migration | diff_summary mentions type change | suspicious |
| DROP CONSTRAINT with no replacement | No ADD CONSTRAINT in same diff | suspicious |
| Migration file added and SQL matches diff | Proper process followed | clean |

## Loop Guard (Rule 7)

| Signal | Detection | Verdict |
|--------|-----------|---------|
| healer_recent_fixes shows same agent ≥2× in 7d | Query healer_recent_fixes WHERE agent_name = X | escalate |
| audit_log shows trainer prompt_update to same agent ≥2× in 7d | audit_log query with 7d window | escalate |

Escalation action: create founder HITL card "Repeated auto-fix loop detected: {agent_name}". Do NOT auto-revert.
Surface the full fix history for founder decision.

## False Positive Guards

Do NOT flag as suspicious:
- Nightly cron runs with zero violations (expected clean state)
- Healer config_patch within ceiling on first attempt for a given incident
- Trainer prompt_update with valid hitl_review_id present
- Backup ref is NULL on read-only action types
