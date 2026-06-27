# Healer — Safety Ceilings and Bounds

Last updated: 2026-06-04
Maintained by: FIX (healer, Agent #16)

Hard limits never exceeded when formulating a config_patch.
Reject any proposed value exceeding these before writing.

## Config Patch Ceilings

| Config key | Max allowed | Default if unset | Rationale |
|-----------|------------|-----------------|-----------|
| backoff_seconds | 600 | 30 | Above 600s, overlapping cron windows cause queue buildup |
| timeout_seconds | 120 | 30 | Railway enforces a 120s hard request timeout |
| max_tokens | 4096 | 1000 | Prevents runaway Opus cost spikes |
| retry_attempts | 5 | 3 | Above 5, errors flood Langfuse and mask the real signal |
| concurrency | 3 | 1 | Supabase connection pool saturates above 3 concurrent agents |

## Loop Guard Threshold

`recent_fix_count >= 2` for the same agent in the past 7 days → MUST escalate. Do NOT patch.
Source: `healer_recent_fixes` view in Supabase.

On loop guard activation:
1. Write `healer_actions.escalated = true`
2. Create founder HITL card: "Loop guard triggered on {agent_name} — {fix_count} fixes in 7 days"
3. Halt without writing any patch

## Cost Ceiling Protocol

If `agent_telemetry.cost_per_run` is within 10% of `agents.yaml.cost_ceiling_usd`:
- Flag in `healer_actions.remediation_notes`
- Recommend config_patch to reduce `max_tokens` or downgrade tier
- NEVER freeze the agent unilaterally — only Auditor can freeze

## Backup Requirement

Every `config_patch` and `prompt_update` MUST:
1. Write timestamped backup to `omerion/backups/<agent>/<timestamp>.<ext>`
2. Record `audit_log.backup_ref` = that backup path
3. Only THEN write the patch

If backup write fails: abort the patch entirely. Do NOT proceed without a backup_ref.

## Confidence Threshold

| Confidence | Action |
|-----------|--------|
| ≥ 0.80 | Apply patch normally |
| 0.65 – 0.79 | Apply with `low_confidence_patch = true` in audit_log |
| < 0.65 | Escalate to founder with diagnosis + proposed patch for human review |
