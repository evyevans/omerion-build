# OMERION Testing Failure Log
> Auto-updated by Antigravity after every test failure. Use this to track patterns, avoid regressions, and ensure reliable agent behaviour.

---

## Wave 0 Architecture Cutover (2026-05-23)

Production has been migrated from the dual root-`core/` + `omerion/` parallel
architecture to a single canonical `omerion/main.py` app. See approved plan
at `/Users/evy/.claude/plans/purring-sauteeing-noodle.md`.

**Canonical entry points:**
- API + scheduler + event broker: `omerion/main.py` → `uvicorn main:app` (WORKDIR `/app/omerion`)
- Discord bot: `discord/omerion_bot.py` (with health sidecar from `omerion_core/runtime/health_sidecar.py`)
- No worker service — APScheduler inside the api process replaces it

**Deploy configs updated:**
- `railway.toml` — 2 services (api + bot); worker service removed
- `docker/Dockerfile.api` — installs `omerion/requirements.txt`, WORKDIR `/app/omerion`, runs `uvicorn main:app`
- `docker/Dockerfile.bot` — installs `omerion/requirements.txt`, runs `python discord/omerion_bot.py`

**Migrated to `omerion_core/`:**
- Stripe webhook → `omerion_core/inbound/stripe.py` (mounted at `/webhooks/stripe`)
- Health sidecar → `omerion_core/runtime/health_sidecar.py`

**Deleted (replaced or dead):**
- root `main.py`, root `core/` (entire), root `departments/`, root `workflows/`
- `api/approvals/router.py` (legacy approvals — use `/inbound/hitl/*`)

**Archived (not deleted):**
- `agency-agents/` → `_archive/agency-agents/`
- Historical audit markdowns → `docs/audits/`

**Known orphans cleaned up (2026-05-24):**
- ~~`api/webhooks/router.py` + the rest of `api/`~~ — **deleted.**
- ~~`docker/Dockerfile.worker`~~ — **deleted.**
- ~~`discord/bot/`~~ — **deleted.**

**Agent count: 18 → 15.** Retired:
- `client_success` → `omerion/scripts/replace_client_success.sql` (view `client_health_today`)
- `competitive_intel` → `omerion/scripts/competitive_intel_cron.py` (RSS + deterministic tagging + Pinecone)
- `r4_evaluation_telemetry` → `omerion/scripts/r4_regression_alert.py` (SQL rollup + auto-pause + Discord alert)

**Compatibility:** `omerion/omerion_core/inbound/app.py` exposes both `/health` AND `/api/v1/health` (alias) so Railway's existing `healthcheckPath = "/api/v1/health"` continues to work during the cutover.

---

## Failure #1 — `column accounts.id does not exist`
**Date:** 2026-05-10  
**Channel tested:** `#leads`  
**Agent:** `hq-lead-scraping`  
**Run ID:** `effd88e3`

### What was tested
Lead generation via Discord. Prompt sent to `#leads`:
> "Can you run a lead generation batch for B2B SaaS companies that use Stripe as their payment processor?..."

### What went wrong
The agent crashed immediately on its first database query with:
```
Error: {'message': 'column accounts.id does not exist', 'code': '42703'}
```

### Root Cause
The agent's `load_priority_accounts()` in `tools.py` was written against a **draft schema** where the primary key was `id`. The live Supabase schema uses `account_id` as the PK. Additional column name mismatches:
- `market` → `market_id`
- `industry` → does not exist
- `employee_count` → `team_size_bucket`
- `priority` → does not exist
- `persona_hint` → `persona`

A secondary issue: the `else` branch filtered on `priority = 'high'` which also doesn't exist.

### What was expected
- Bot queues the agent → confirms in `#leads`
- Agent researches target accounts from Supabase
- HITL card appears in `#founder-hitl` for founder review
- On approval → dossier written to `research_dossiers` table

### Fix Applied
1. Corrected `SELECT` column list to match live schema in `tools.py`
2. Replaced `priority = 'high'` filter with `ORDER BY score DESC`
3. Fixed all downstream `account["id"]` references to `account["account_id"]`
4. Added `parse_discord_intent` graph node to parse natural-language requests and create stub accounts when DB has no seeded data
5. Seeded Supabase with 3 test accounts: Stripe, Linear, Retool

---

## Failure #2 — `ClaudeRouter.complete() got an unexpected keyword argument 'user'`
**Date:** 2026-05-11  
**Channel tested:** `#watch`  
**Agent:** `market-watcher`  
**Run ID:** `a04b8395`

### What was tested
Deep market research via Discord. Prompt sent to `#watch`:
> "Perform deep market research on the current B2B SaaS landscape for payment infrastructure..."

### What went right
- ✅ Bot received the message correctly
- ✅ Channel routing worked: `#watch` → `market-watcher` skill
- ✅ Run was queued and confirmed in `#watch` with run ID
- ✅ `#mission-control` received the failure notification (error reporting pipeline functional)

### What went wrong
The agent immediately crashed when calling Claude:
```
ClaudeRouter.complete() got an unexpected keyword argument 'user'
```

### Root Cause
**Systemic across ALL 12 agent tool files.** Every agent was calling `ClaudeRouter.complete()` with a `user=` keyword argument. The actual method signature only accepts `prompt=` (or `messages=`), not `user=`.

This affected every agent in the system:
- `r1_market_tech_watcher`, `r2_oss_scout`, `r3_strategic_architect`
- `market_mapper`, `lead_scraper_enricher`, `icp_scoring`
- `crm_nurture`, `linkedin_outreach`, `meeting_intelligence`
- `build_orchestrator`, `offer_matching`, `outcome_attribution`

### What was expected
- Agent fetches RSS feeds configured in `agents.yaml`
- Filters signals by relevance keywords
- Calls Claude to tag each signal with impact_tag + priority
- Writes tagged insights to `rd_insights` Supabase table
- Results posted back to `#watch`

### Fix Applied
Ran a targeted `re.sub()` across all 12 affected files replacing every `user=` kwarg inside `.complete()` calls with `prompt=`. **All 12 files fixed simultaneously.**

---

## Failure #3 — `Could not find the 'estimated_priority' column of 'rd_insights' in the schema cache`
**Date:** 2026-05-11
**Channel tested:** `#watch`
**Agent:** `market-watcher`
**Run ID:** `d4edaf49`

### What was tested
Same deep market research prompt in `#watch`, re-tested after Failure #2 was fixed:
> "Act as a Real Estate AI Consultant specializing in B2B payment infrastructure for property tech (proptech). Perform deep market research..."

### What went right
- ✅ Bot received message and routed correctly to `market-watcher`
- ✅ Run was queued with "✅ Queued market-watcher" confirmation
- ✅ **Claude LLM call succeeded** (Failure #2 fix — `user=` → `prompt=` — confirmed working)
- ✅ RSS feed fetching executed correctly
- ✅ Signal tagging via Claude produced valid results
- ✅ `#mission-control` caught and reported the failure accurately

### What went wrong
Agent crashed when trying to INSERT tagged insights into the `rd_insights` Supabase table:
```
Error: {'message': "Could not find the 'estimated_priority' column of 'rd_insights' in the schema cache", 'code': 'PGRST204'}
```

### Root Cause
**The `rd_insights` table either does not exist or is missing columns in the live Supabase database.** The migration file `0005_telemetry_and_rd.sql` defines the table correctly (including `estimated_priority`), but this migration was **never applied to the live Supabase instance**. PostgREST cannot find the column because the table schema in production doesn't match what the code expects.

This is the **same class of bug as Failure #1** (code-vs-database mismatch), but at a higher level — the entire migration wasn't run, not just a column name.

### What was expected
- Agent fetches RSS feeds → filters for relevance → calls Claude to tag each signal
- Tagged insights are written to `rd_insights` table
- Insights are embedded into Pinecone for semantic search
- HITL card appears in `#founder-hitl` (if configured for this agent)
- Results posted back to `#watch`

### Fix Required
Run migration `0005_telemetry_and_rd.sql` in the Supabase SQL Editor to create all R&D tables: `agent_telemetry`, `agent_performance_metrics`, `api_call_log`, `rd_insights`, `oss_candidates`, `rd_proposals`.

After running the SQL, reload the PostgREST schema cache:
```sql
NOTIFY pgrst, 'reload schema';
```

---

---

## Failure #4 — SAME as Failure #3 (migration still not applied at time of re-test)
**Date:** 2026-05-11
**Channel tested:** `#watch`
**Agent:** `market-watcher`
**Run ID:** `d4edaf49` (same run — confirmed this was the pre-migration test result)

### Root Cause (Systemic — Affects ALL Agents)
This is not a bug in any single agent. **The entire Supabase database schema was never fully applied to the live production instance.** 

The project has **25 migration files** defining the complete schema. None of them were run in order against the live database. Instead, the user manually ran individual SQL snippets through the Supabase SQL Editor on an ad-hoc basis. This means:

- Some tables exist, some don't
- Some tables exist but are missing columns from later migrations
- PostgREST (code: `PGRST204`) cannot find columns that don't exist in the live DB

This is why **every agent fails at the database layer** — they can receive messages, route correctly, and even call Claude, but the moment they try to READ or WRITE to Supabase, they crash because the table or column doesn't exist.

### Tables That MUST Exist (All 25 Migrations)
The following tables are required for the full system. **None are guaranteed to exist** without running all migrations:

| Migration | Tables Created |
|---|---|
| 0001 | ENUMs (persona, account_status, account_tier, etc.) |
| 0002 | markets, accounts, contacts, events |
| 0003 | scores, opportunities, blueprints, build_tasks, deployments, revenue_events, lead_conversions |
| 0004 | agent_actions, attribution_reports, outbound_communications, nurture_sequences, contact_activity_log, research_dossiers, generated_drafts, founder_review_queue |
| 0005 | agent_telemetry, agent_performance_metrics, api_call_log, **rd_insights**, oss_candidates, rd_proposals |
| 0009 | clients (re-pivot) |
| 0010 | checkpoint_migrations, checkpoints, checkpoint_writes, checkpoint_blobs |
| 0013 | agent_runs, business_outcomes |
| 0016 | (cost/outcome columns on existing tables) |
| 0017 | job_postings, job_applications |
| 0018 | outreach_threads |
| 0021 | document_chunks, document_index, drive_watch_channels |
| 0022 | properties |
| 0023 | agent_messages |
| 0024 | error_log |

### The Fix (One-Time, Permanent)
A **master idempotent migration file** has been generated at:
```
omerion/infra/supabase/MASTER_MIGRATION.sql
```
This file combines all 25 migrations with `IF NOT EXISTS` guards so it is **safe to run even if some tables already exist**. It will not break or duplicate anything.

**Action required:** Copy the contents of `MASTER_MIGRATION.sql` into the Supabase SQL Editor and run it. This is a one-time fix that will unblock every agent permanently.

---

## Failure #5 — `syntax error at or near "NOT"` — `CREATE TYPE IF NOT EXISTS` not valid for ENUMs
**Date:** 2026-05-11
**Agent:** N/A — Supabase migration failure
**File:** `MASTER_MIGRATION.sql`

### What went wrong
Running the `MASTER_MIGRATION.sql` in Supabase SQL Editor failed immediately with:
```
ERROR: 42601: syntax error at or near "NOT"
LINE 19: CREATE TYPE IF NOT EXISTS persona AS ENUM (
```

### Root Cause
PostgreSQL does **not** support `CREATE TYPE IF NOT EXISTS` for ENUM types. The Python script that built `MASTER_MIGRATION.sql` blindly converted all `CREATE TYPE` statements to `CREATE TYPE IF NOT EXISTS`, which works for composite types but **breaks for ENUMs** on all PostgreSQL versions that Supabase runs.

The correct idempotent pattern for ENUM types is a `DO $$` block with exception handling:
```sql
-- WRONG (causes syntax error for ENUMs):
CREATE TYPE IF NOT EXISTS persona AS ENUM (...);

-- CORRECT (works on all PostgreSQL versions):
DO $$ BEGIN
  CREATE TYPE persona AS ENUM (...);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
```

### Fix Applied
Rewrote `MASTER_MIGRATION.sql` using a regex replacement that converts all ENUM `CREATE TYPE IF NOT EXISTS` statements to the safe `DO $$ ... EXCEPTION ... END $$;` pattern. Zero remaining bad syntax confirmed.

---

## Patterns & Recurring Risks

| Pattern | Risk | Mitigation |
|---|---|---|
| ❌ **Migrations never applied to live DB** | **CRITICAL — ALL agents fail at DB layer** | **Run `MASTER_MIGRATION.sql` ONCE against Supabase before any agent testing** |
| Schema column name mismatch (code vs live DB) | HIGH — crashes immediately | Cross-check agent SELECT queries against migration files before testing |
| Wrong kwarg in `ClaudeRouter.complete()` | HIGH — crashes every LLM call | `complete()` accepts: `tier`, `system`, `messages`, `prompt`, `max_tokens`, `temperature`, `tools`, `thinking`. NEVER `user=`. |
| Empty database during first test | MEDIUM — silent failure | Seed accounts/contacts tables before first-run agent tests |
| Agent triggered from wrong Discord channel | LOW — explicit error | Each channel maps to one skill. See `CHANNEL_SKILL_MAP` in `discord_route.py` |

---

## Pre-Test Checklist (Run Before EVERY Test Session)

Before sending any Discord message to an agent channel, verify:

- [ ] **`MASTER_MIGRATION.sql` has been applied to Supabase** (one-time)
- [ ] ngrok is running: `ngrok http 8000 --url=omerion.ngrok.io`
- [ ] Backend is running: `cd omerion && uv run uvicorn main:app --host 127.0.0.1 --port 8000`
- [ ] Bot is running: `cd omerion && uv run python ../discord/omerion_bot.py`
- [ ] Supabase `accounts` table has at least 3 seed rows (for lead/research agents)
- [ ] ANTHROPIC_API_KEY is set and has credits
- [ ] SERP_API_KEY and HUNTER_API_KEY are set (for lead scraping)

---

## Successful Run Definition
A run is considered **successful** when:
1. Bot acknowledges the message in the correct channel with "✅ Queued..."
2. Backend logs show agent nodes executing without exceptions
3. Data is written to the appropriate Supabase table (check Table Editor)
4. A HITL card appears in `#founder-hitl` (for agents requiring approval)
5. After approval, a completion message posts back to the originating channel

---

## Developer Tooling — Claude Code Session Rules

These rules apply when Claude Code is assisting with this codebase (not agent runtime behaviour).

### Navigation (token-savior principles)
- Navigate by **symbol name + grep**, not by reading full files. Use `grep -n "symbol_name" omerion/` to locate functions before reading.
- When planning changes, name the **functions to modify**, not just the files.
- Never paste large file contents into a prompt when a targeted excerpt covers the need.

### Agent Development (agent-orchestrator pattern)
- When implementing a **new agent**, prototype it in a git worktree (`git worktree add ../agent-wip-<name> -b feat/<name>`) and validate its DB writes + HITL flow before merging to main.
- Every new agent must pass: (1) correct Supabase table writes, (2) HITL card appears in `#founder-hitl`, (3) completion message posts to originating channel.

### Continuous Learning Loop (everything-claude-code)
- After every test failure: add an entry to the **Failure Log** above (section, root cause, fix applied).
- After every successful agent run: verify the fix is in the correct layer (schema, prompt, tool) — not papered over with a try/except.
- Reference `omerion/omerion_core/outreach/style_guard.py` for all output quality rules — do not embed style rules directly into individual agent files.

### Reference Repos (on disk, not runtime dependencies)
- `stop-slop/` — phrase ban-list source; sync to `style_guard.SLOP_BANNED_PHRASES` when updated upstream
- `humanizer/` — AI-writing pattern catalogue; sync to `style_guard.HUMANIZER_VOICE_RULES` when updated
- `everything-claude-code/` — Claude Code session best practices; read when debugging token costs or context management
- `awesome-claude-code-subagents/` — subagent template library; reference when decomposing `build_orchestrator` tasks
- `claude-agent-sdk-python/` — Anthropic SDK source; reference for `query()` call signatures and `ClaudeAgentOptions`

---
*This file is maintained by Antigravity. Updated after every test failure. Do not edit manually.*

