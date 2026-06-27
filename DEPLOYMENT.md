# Railway deployment — Omerion Agentic Workflow Factory

Three services, one repo, one Dockerfile each.

## Prerequisites

- Railway account + project created.
- Supabase project provisioned (URL + service role key in hand).
- Anthropic API key.
- Discord bot created (token + app id), HITL channel ID.
- Pinecone index `omerion-rag` created.
- Optional: Langfuse project (mandatory in `RUNTIME_ENV=prod`).

## One-time setup

1. **Apply database migrations** in the Supabase SQL editor in this order:
   - `migrations/0026_clients_multitenant.sql`
   - `migrations/0027_contacts_persona_label.sql`
   - `migrations/0028_agent_runs_universal.sql`
   Verify: `select count(*) from agent_runs;` returns `0` without error.

2. **Push the repo to GitHub** (Railway can also import directly).

3. **In Railway, link the repo** → Railway auto-detects `railway.toml` and
   creates three services:
   - `omerion-api`
   - `omerion-worker`
   - `omerion-discord-bot`

4. **Set environment variables** on all three services (paste from
   `.env.example`). The same variables apply to every service; you can
   define them at the project level so the three services share them.

5. **First deploy** — Railway builds each Dockerfile and starts the
   service. Watch the build logs for any pip resolution issues.

## Per-service deploy commands

| Service                | Start command                                          |
| ---------------------- | ------------------------------------------------------ |
| `omerion-api`          | `uvicorn main:app --host 0.0.0.0 --port $PORT`         |
| `omerion-worker`       | `python -m core.runtime.worker_main`                   |
| `omerion-discord-bot`  | `python discord/bot/bot.py`                            |

> The Discord bot is run as a **script** (not `-m`) on purpose. The
> repo has a local `discord/` folder that shadows the discord.py library
> when imported via the module system. Running as a script puts only
> `discord/bot/` on `sys.path`, so `import discord` resolves cleanly to
> the installed library.

## Healthchecks

- `omerion-api` exposes `GET /api/v1/health` → returns
  `{ok: true, agents_registered: N}`. Railway is configured to use this.
- `omerion-worker` and `omerion-discord-bot` have no HTTP listener — rely
  on Railway's `ON_FAILURE` restart policy.

## Smoke test after deploy

1. **API up**: `curl -fsS https://<api-domain>/api/v1/health`
2. **Auth**: same call without `Authorization: Bearer $OMERION_WEBHOOK_TOKEN`
   should 401 once you set the token.
3. **Agent dry-run**:
   ```bash
   curl -X POST https://<api-domain>/api/v1/agents/icp_scorer/run \
     -H "Authorization: Bearer $OMERION_WEBHOOK_TOKEN" \
     -H "X-Omerion-Client: omerion-internal" \
     -H "Content-Type: application/json" \
      -d '{"lead": {"persona":"sme_founder","company":{"team_size":12,"metro":"Toronto"}}}'
   ```
   Expected: `200` with `output.confidence > 0` and a tier letter.
4. **RSI trigger**:
   ```bash
   curl -X POST https://<api-domain>/api/v1/webhooks/rsi/trigger \
     -H "Authorization: Bearer $OMERION_WEBHOOK_TOKEN" \
     -H "X-Omerion-Client: omerion-internal" \
     -d '{"hours": 24}'
   ```
   Expected: `200` and a `synthesis.top_actions` array (possibly empty
   if telemetry is cold).

## Rollback

Each Railway deploy is immutable. To roll back, click "redeploy" on the
last good deploy in the Railway UI. Database migrations are forward-only
— do not roll back the SQL files without a paired down-migration.

## Scheduling

All scheduled loops (hourly observer, weekly RSI) are owned by
`omerion-worker` via APScheduler (in-process). Set
`ENABLE_WORKER_SCHEDULER=true` (default) to activate. No external
scheduler is required.
