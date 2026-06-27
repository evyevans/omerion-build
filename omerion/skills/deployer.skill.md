---
name: deployer
tier: A
agent_number: 18
graph: agents.deployer.graph:build
triggers:
  - event:deployment.live
events_consumed:
  - deployment.live
events_emitted:
  - deployment.health_confirmed
  - deployment.health_failed
hitl: conditional                 # G3 (deploy/infra) — fires only when pending migrations exist
model_tier: none                  # deterministic pipeline — no LLM cognition
rate_limits:
  - railway
  - supabase_management
  - anthropic
---

# DEPLOYER — Infrastructure Provisioner (Agentic Factory Agent #18)

## Identity & Scope

DEPLOYER is the final release engineer of the Omerion Agentic Factory. It
receives the `deployment.live` event from RUN (build_orchestrator) after all
PRs have merged and the Founder has approved the deployment, then executes the
deterministic infrastructure pipeline:

1. Backs up the database
2. Runs pending SQL migrations
3. Provisions the cloud container (Railway)
4. Resolves the live health URL
5. Smoke-tests the live endpoint (60 s timeout)
6. Rolls back automatically on failure

DEPLOYER does **not** write code, approve deployments, or set strategy.
Its only job is safe, verifiable infrastructure delivery.

## Trigger & Input Contract

**Event consumed:** `deployment.live`

Required payload fields:
- `deployment_id` (UUID) — matches the `deployments` table PK
- `client_id` (UUID) — client this deployment serves
- `blueprint_id` (UUID, optional) — originating blueprint

**State initialised from event:**
```python
DeployerState(
    deployment_id=...,
    client_id=...,
    blueprint_id=...,
    correlation_id=...,   # propagated from upstream event
)
```

## Reasoning Chain (deterministic infra pipeline + conditional G3 gate)

### Node 1 — `backup_database`
Call Supabase Management API `POST /v1/projects/{ref}/database/backups`.
Store `backup_ref` in state. **Hard stop on failure** — nothing proceeds
without a verified, PITR-restorable backup.

### Node 2a — `discover_migrations`
Discover pending migration SQL (after the backup). Stash `(filename, sql)` pairs.
Routing: dir error → `emit` (health_failed); **no pending migrations → `provision_cloud_run`
(gate skipped — code-only deploy)**; pending migrations → `hitl_gate`.

### Node 2b — `hitl_gate` (G3 — deploy/infra; conditional)
Reached only when pending migrations exist. Routes through the global HITL policy
`gate(Gate.DEPLOY_OR_INFRA, …)`, showing the **actual migration SQL** with a note that
the backup is already taken. **Fail-closed:** reject → nothing migrates, `emit`
(health_failed, `migration_rejected`). Approve → `apply_migrations`.

### Node 2c — `apply_migrations`
Run the founder-approved migrations via the Supabase Management API. On failure:
`outcome = "health_failed"`, `failure_reason = "migration_error"`, **short-circuit to
`emit`** — cloud provisioning NEVER runs after a migration error (guardrail #2).

### Node 3 — `provision_cloud_run`
Call Railway GraphQL API `serviceInstanceRedeploy` mutation for the
configured `railway_service_id`. Capture `live_url` from domain query.
If provision fails: short-circuit to `emit` with `failure_reason = "provision_error"`.

### Node 4 — `update_dns`
Compose `health_url = live_url + "/api/v1/health"`. In a multi-region setup
this node would also update DNS CNAME records via the GCP API.

### Node 5 — `run_smoke_tests`
`httpx.get(health_url, timeout=60.0)`.
- HTTP 200 → `smoke_ok = True` → route to `emit` (happy path)
- Any other response or timeout → `smoke_ok = False` → route to `rollback`

Guardrail #3: `deployment.health_failed` is emitted immediately on any
response that is not HTTP 200 within 60 seconds. There is no retry.

### Node 6a — `rollback` (conditional branch; fully deterministic — NO LLM)
Triggered only when `smoke_ok` is False. Rolls back to the known-good pre-deploy
state, **INVARIANT: restore DB before reverting the container** (data integrity
first; avoids old code against a migrated schema):
1. If a migration applied (`migration_ok`) and a `backup_ref` exists → Supabase PITR
   restore. (A smoke failure means the deploy never served real traffic — smoke is the
   first request — so the restore loses no production data.)
2. If `provision_ok` → Railway `deploymentRollback` mutation (revert container).

Sets `rollback_ok`; routes to `emit`. No LLM is invoked in the incident path —
rollback must be predictable, not improvised.

### Node 6b — `emit`
Persists a row to `deployer_health_log`. Updates `deployments.status`.
Emits the terminal event:
- `deployment.health_confirmed` → downstream outcome-attribution starts KPI measurement
- `deployment.health_failed` → downstream healer / founder-hitl is notified

## Output Contract

**On success:**
- `deployer_health_log` row with `outcome = "confirmed"`
- `deployments.status = "live"`
- Event: `deployment.health_confirmed`

**On failure:**
- `deployer_health_log` row with `outcome ∈ {health_failed, rollback_ok, rollback_failed}`
- `deployments.status = "failed"`
- Event: `deployment.health_failed` with `failure_reason` and `rollback_ok`

## Stop Conditions

| Condition | Action |
|---|---|
| Backup API returns error | `RuntimeError` — pipeline aborts, no migration runs |
| Migration SQL returns error | Short-circuit to `emit` with `failure_reason = "migration_error"` |
| Railway provision returns error | Short-circuit to `emit` with `failure_reason = "provision_error"` |
| Health endpoint not HTTP 200 in 60 s | Route to `rollback`, then `emit` with `failure_reason = "smoke_timeout"` |
| Rollback also fails | `emit` with `outcome = "rollback_failed"` — manual intervention required |

## Idempotency Rules

- `deployer_health_log` uses a synthetic PK (`gen_random_uuid()`). Re-running
  DEPLOYER for the same `deployment_id` inserts a second row; this is
  intentional — it records each attempt independently.
- `emit_event` calls use the `natural_key` property for deduplication:
  `deployment.health_confirmed:{deployment_id}` and
  `deployment.health_failed:{deployment_id}`. The idempotency layer
  deduplicates on the same `deployment_id` within a time window.

## Fallback Protocol

If `backup_database` raises, the Python exception propagates to the
`agent_wrapper`. The wrapper records the failure in `agent_runs`, emits
a `regression.alert`, and notifies `#mission-control`. No partial state
is written to Supabase.

If the Railway API is unreachable during provision, `provision_ok = False`
and DEPLOYER emits `deployment.health_failed` without attempting rollback
(there is nothing to roll back).

## Cognition Rationale

**No LLM cognition — by design.** The entire pipeline is deterministic API calls
+ boolean state transitions. You do not want a model autonomously deciding how to
migrate or roll back a production database; the safe paths are hard-coded and the
3 guardrails are enforced at the graph level. The only human judgment is the **G3
gate on pending migrations**, where the founder approves the actual SQL.
(Rollback was previously documented as LLM-driven, but that call targeted a
non-existent `Tier.SONNET` and never ran — it is now explicitly deterministic.)

## Observability

- Langfuse trace prefix: `deploy.*`
- Key metrics: `deployer.backup_ok`, `deployer.migration_ok`,
  `deployer.provision_ok`, `deployer.smoke_ok`, `deployer.rollback_ok`
- All nodes use `@traced_node` → cost and latency tracked per node
- `deployer_health_log` is the canonical audit trail for every run

## Config Reference

```yaml
# config/agents.yaml → deployer:
railway_service_id: "${RAILWAY_SERVICE_ID}"
smoke_test_timeout_s: 60
health_path: "/api/v1/health"
rollback_on_failure: true
```

Environment variables (Railway or .env):
- `RAILWAY_API_TOKEN` — Railway personal token
- `RAILWAY_PROJECT_ID` — Railway project ID
- `RAILWAY_SERVICE_ID` — target service for deployments
- `SUPABASE_MANAGEMENT_TOKEN` — Supabase Management API token
- `SUPABASE_PROJECT_REF` — Supabase project reference ID
