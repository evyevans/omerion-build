# OMERION Operations Runbook

> Living document. Update after every incident, deploy, or new procedure.
> Authoritative source: `/Users/evy/.claude/plans/purring-sauteeing-noodle.md`.

## Quick reference

| Need to | Section |
|--|--|
| Deploy a fix to prod | [Deploy procedure](#deploy-procedure) |
| Roll back a bad deploy | [Rollback](#rollback) |
| Investigate a stuck run | [Stuck run triage](#stuck-run-triage) |
| Replay a dead-letter event | [DLQ replay](#dlq-replay) |
| Cost spike alert went off | [Cost spike investigation](#cost-spike-investigation) |
| Add a new agent | [New agent checklist](#new-agent-checklist) |
| Verify the wrapper is active | [Wrapper sanity check](#wrapper-sanity-check) |

---

## System overview

- **API + scheduler + broker:** `omerion/main.py` — runs as `omerion-api` on Railway.
  - HTTP routes: `/inbound/*`, `/webhooks/*`, `/agents/*`, `/reports/*`, `/mission-control`, `/health`.
  - APScheduler hosts the agent cron jobs + sweeper + Mission Control alerts.
  - Supabase Realtime broker subscribes to events and dispatches downstream agents.
- **Discord bot:** `discord/omerion_bot.py` — runs as `omerion-discord-bot` on Railway.
  - Health sidecar at `$BOT_HEALTH_PORT` (default 8002).
- **15 agents** registered in `omerion/agents/__init__.py`.
- **3 retired "agents"** now run as deterministic scripts/views in `omerion/scripts/`.

### Trust boundary

Every agent invocation MUST pass through `agent_wrapper.run()` in
`omerion/omerion_core/runtime/agent_wrapper.py`. The wrapper enforces:

1. **Input validation** (Pydantic per registered contract)
2. **Idempotency dedupe** (run_key — content + minute window)
3. **Mutex** per `(skill, business_entity_id)`
4. **Opt-out cohort filter** (`optout.is_opted_out`)
5. **Cost-budget pre-check** (per-skill per-day cap)
6. **AI execution** (delegates to `run_executor.execute_run`)
7. **Output schema validation** (Pydantic per contract)
8. **Style-guard hard filter** on `human_facing_drafts`
9. **Recipient verification** — every recipient_id MUST be in the filtered cohort
10. **Value-bound enforcement** — over-cap proposals → HITL
11. **Confidence threshold** — below floor → HITL
12. **Typed handoff emission** via `broker.emit_typed`

If you bypass the wrapper, you've shipped a regression. See
`omerion/agents/MIGRATION_PATTERN.md` for the per-agent recipe.

---

## Deploy procedure

1. Run the contract tests locally:
   ```
   cd omerion && python -m pytest tests/unit/test_wave_2_contracts.py -v
   ```
2. Push to the deploy branch. Railway's CI builds Docker images from
   `docker/Dockerfile.api` and `docker/Dockerfile.bot`.
3. Watch the `omerion-api` service's `/api/v1/health` endpoint flip to 200.
   - 503 with `db: down` → Supabase credential issue.
   - 503 with `scheduler: down` → APScheduler boot failed; check logs.
   - 503 with `broker: down` → Supabase Realtime channel didn't subscribe.
4. Verify the Discord bot reconnects: check `omerion-discord-bot` /health on $BOT_HEALTH_PORT.
5. Send a test message in `#scout` (or any agent channel). Confirm:
   - An `agent_runs` row appears with status='running' then 'completed'.
   - An `agent_messages` row exists for any downstream handoff.
   - The Discord channel gets a completion reply.

## Rollback

1. Railway UI → `omerion-api` → Deployments → previous successful
   → **Redeploy**. Same for the bot.
2. If the bad deploy wrote DB rows you need to undo:
   - `agent_runs`: set `status='cancelled'`, `error='manual_rollback'`.
   - `business_outcomes` from a bad source: filter
     `metadata->>'source' = 'agent_inference'` (should be zero — Wave 2.3
     blocks them; if any exist, contract regression).
   - `opportunities` with bad value: filter
     `metadata->>'value_source' != 'deterministic_midpoint'`.
3. Migrate config rollbacks via SQL in Supabase SQL Editor; never via
   ad-hoc REST PATCH (no audit trail).

---

## Stuck run triage

**The sweeper closes stuck runs every 5 minutes.** If you see a stuck
run anyway, the sweeper itself may be down — check the scheduler.

```sql
-- Currently stuck (post-sweep residual)
SELECT run_id, agent_name, status, started_at, correlation_id
FROM agent_runs
WHERE (status = 'running' AND started_at < now() - interval '30 minutes')
   OR (status = 'hitl_waiting' AND created_at < now() - interval '48 hours')
ORDER BY started_at;
```

To manually close a single stuck run:

```sql
UPDATE agent_runs
SET status = 'failed',
    error = 'manual_close: ' || $reason,
    finished_at = now(),
    superseded_at = now()
WHERE run_id = $RUN_ID;
```

The `superseded_at` flag prevents zombie ThreadPoolExecutor threads
from later transitioning the run back to running.

---

## DLQ replay

```sql
-- All currently-pending DLQ rows
SELECT dlq_id, event_type, target_agent, attempt_count, next_retry_at, last_error
FROM event_dead_letter
WHERE status = 'pending'
ORDER BY created_at;

-- Force a manual retry (resets the backoff)
UPDATE event_dead_letter
SET next_retry_at = now()
WHERE dlq_id = $DLQ_ID;
```

The sweeper's `sweep_dead_letter_queue()` runs every 10 minutes. Force
a retry by setting `next_retry_at = now()`; the next tick picks it up.

After `max_attempts` (default 5), a row is parked at
`status = 'permanent_failure'`. Inspect the payload, decide whether to:
- **Re-emit manually:** set status='pending' and reset attempt_count, OR
- **Drop:** set status='abandoned' with a note in last_error.

---

## Cost spike investigation

Triggered when 1-hour spend > 2× 7-day hourly average.

```sql
-- What spent the money in the last hour?
SELECT agent_name, COUNT(*) AS runs, ROUND(SUM(cost_usd)::numeric, 2) AS spend_usd
FROM agent_runs
WHERE started_at >= now() - interval '1 hour'
GROUP BY agent_name
ORDER BY spend_usd DESC;

-- Most expensive single runs in the last hour
SELECT run_id, agent_name, cost_usd, prompt_tokens, completion_tokens
FROM agent_runs
WHERE started_at >= now() - interval '1 hour'
ORDER BY cost_usd DESC NULLS LAST
LIMIT 10;
```

If one agent is the culprit, kill-switch it:

```sql
INSERT INTO agent_config (agent_name, schedule_enabled, paused_reason, paused_at)
VALUES ($AGENT, false, 'cost_spike_manual_pause', now())
ON CONFLICT (agent_name) DO UPDATE
  SET schedule_enabled = false,
      paused_reason = EXCLUDED.paused_reason,
      paused_at = now();
```

The wrapper's `_is_agent_paused` check refuses to dispatch paused
agents. Re-enable by setting `schedule_enabled = true`.

---

## Wrapper sanity check

A quick five-line check the trust boundary is working:

1. **Idempotency:**
   ```
   POST /inbound/discord/route  (same content, twice within 60s)
   → first run: agent_runs row inserted
   → second run: log shows "wrapper_idempotency_dedup"
   ```

2. **Opt-out:**
   ```sql
   UPDATE contacts SET do_not_contact = true WHERE contact_id = $TEST;
   ```
   Trigger an outreach agent against a cohort that includes $TEST. The
   wrapper logs `wrapper_cohort_optout_filtered` with $TEST in dropped.

3. **Style guard:**
   ```
   # Mock or force an output containing "Let me be clear" → wrapper
   # logs "wrapper_post_validation_style" and the run → 'failed' (not
   # 'completed'); the offending draft surfaces in HITL.
   ```

4. **Value bound:**
   ```
   # Force offer-matching to produce a >$250k value → wrapper logs
   # "wrapper_value_bound_to_hitl" and the run → 'hitl_waiting'.
   ```

5. **Recipient verification:**
   ```
   # Force an agent to emit a recipient_id NOT in the input cohort →
   # wrapper logs "wrapper_recipient_not_in_cohort" and run → 'failed'.
   ```

If any of these fail to behave as described, the wrapper has been
bypassed or regressed. See `omerion_core/runtime/agent_wrapper.py`.

---

## New agent checklist

Per `omerion/agents/MIGRATION_PATTERN.md`:

1. `omerion/agents/<name>/contracts.py` — Pydantic input/output + AgentContract.
2. `omerion/agents/<name>/__init__.py` — `from . import contracts` BEFORE `register(...)`.
3. `omerion/agents/<name>/graph.py` — must respect `state.cohort` as the only
   legal recipient set. Must NOT import the Supabase client directly for
   writes — use repository functions or emit events.
4. Test: send a Discord message to the agent's channel and verify the
   wrapper logs appear (see [Wrapper sanity check](#wrapper-sanity-check)).
5. Register the agent in `omerion/agents/__init__.py` imports list.

---

## Event schemas

When adding a new event:

1. Add to `EventType` enum in `omerion/omerion_core/events/bus.py`.
2. Define a Pydantic schema in `omerion/omerion_core/events/schemas.py`
   and register it in `EVENT_SCHEMAS`.
3. Add the subscription map in `omerion/omerion_core/events/broker.py`
   `EVENT_SUBSCRIPTIONS` if it has downstream consumers.
4. Run `pytest tests/unit/test_wave_2_contracts.py::TestEventSchemas` — the
   `test_all_subscribed_events_have_schemas` test catches missed schemas.

---

## Migrations

- New migrations go in `omerion/infra/supabase/migrations/00XX_<name>.sql`.
- Idempotent guards (`IF NOT EXISTS`) on every CREATE/ALTER.
- ENUM creates use the `DO $$ EXCEPTION WHEN duplicate_object ...` pattern
  per migration `0026` precedent.
- Apply to staging first, verify by running the contract tests against
  the staging DB. Then apply to prod via Supabase SQL Editor.

---

## Failure injection drills

Quarterly tabletop:

1. **Kill the broker mid-handoff.** Expected: agent_messages write succeeds
   for the in-flight event; the next scheduled tick of the sweeper observes
   no stuck runs. (Broker auto-reconnects via `start_broker`.)
2. **Kill the scheduler.** Expected: `/api/v1/health` flips to 503;
   Railway restarts the container; APScheduler resumes; sweeper catches
   up via the `coalesce=True` on the jobs.
3. **Discord webhook returns 500.** Expected: `notification_outbox` rows
   accumulate; sweeper retries with exponential backoff; alert fires
   when retry count crosses threshold.
4. **Duplicate Discord message within 60s.** Expected: second run is
   deduped via the wrapper's idempotency_key check; logs show
   `wrapper_idempotency_dedup`.
5. **Inject malformed LLM output.** Expected: wrapper raises pydantic
   ValidationError → run → 'failed' with a `output_validation:...` error.

---

*Last updated: Wave 3 + 4 ship. See `purring-sauteeing-noodle.md` for
the architectural intent and `CLAUDE.md` for incident history.*
