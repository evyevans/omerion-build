# Deployer — Rollback Runbook

Last updated: 2026-06-04
Maintained by: DEPLOY (deployer, Agent #18)

DEPLOYER triggers automatic rollback when smoke_test fails after provisioning.
This runbook covers manual recovery when the automated rollback itself fails.

## Automated Rollback (DEPLOYER handles this)

1. `railway_rollback_deployment` MCP tool → reverts to previous successful deployment
2. If migration was applied: execute `omerion/infra/supabase/migrations/<N>_down.sql` if it exists
3. Update `deployer_health_log.rollback_ok = true/false`
4. Emit `DEPLOYMENT_ROLLBACK_COMPLETED` event

## Manual Recovery (founder action required)

### Step 1 — Identify the failure

```sql
SELECT * FROM deployer_health_log ORDER BY created_at DESC LIMIT 5;
```

Note: `deployment_id`, `backup_ref`, which step failed (`migration_ok` / `provision_ok` / `smoke_ok`).

### Step 2 — Railway revert (when provision_ok = false)

Railway dashboard → project → deployment history → last known-good deployment → Redeploy.
Verify: `GET /api/v1/health` returns HTTP 200 within 30s.

### Step 3 — Supabase point-in-time restore (when migration caused data corruption)

1. Supabase dashboard → Database → Backups
2. Select timestamp BEFORE the failed migration run
3. Restore to a new branch first; verify data integrity; promote to main
4. **This is destructive — confirm with founder before executing.**

### Step 4 — DNS / domain fallback

If Railway is unresponsive after rollback:
- Switch `healthcheckPath` in `railway.toml` to `/health` (alias path)
- Redeploy via `railway up` from local if MCP is unavailable

## Escalation Path

`rollback_failed` status in `deployer_health_log` → manual founder recovery required.
Do NOT attempt automated re-deployment after a failed rollback without diagnosing root cause first.
