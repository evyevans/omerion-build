# R1 TRACK — Console setup & failure recovery

## What went wrong (2026-06-26 session)

The managed agent ran `echo $SUPABASE_URL` / `echo $SUPABASE_SERVICE_ROLE_KEY` and both were
**EMPTY** in the Anthropic runtime. It correctly stopped with `r1_credential_unresolved`.

Secondary issues:
- Console skill was still **stale** (Pinecone + OpenAI embeddings + dual-threshold dedup).
- Trigger message still demanded `pinecone_upserts` parity.
- Supabase credentials were never bound in the agent **Credential vault / Environment**.

## Fix — two paths (pick one)

### Path A — Omerion bridge (recommended)

Managed agent POSTs tagged rows to your Railway API; the server writes to Supabase using
credentials already on `omerion-api`.

**1. Deploy** the latest `omerion-api` (includes `POST /internal/rd/insights`).

**2. Anthropic Console → Credential vaults**

Create secret:
- Name: `OMERION_WEBHOOK_TOKEN`
- Value: same value as Railway variable `OMERION_WEBHOOK_TOKEN` on `omerion-api`

**3. Managed Agents → your R1 deployment → Environment**

Bind vault secret `OMERION_WEBHOOK_TOKEN` to the agent environment.

**4. Skills** — upload `r1-market-tech-watcher.skill` (zip) via Console → Skills → Update Skill.
   Source folder: `skill-package/r1-market-tech-watcher/` (contains `SKILL.md` + `references/`).
   Do NOT use the old markdown paste flow; the platform expects a `.skill` or `.zip` directory upload.

**5. Agent YAML** — paste `managed_agent.console.yaml`.

**6. Trigger** — paste `TRIGGER_MESSAGE.txt` (update run_date if needed).

### Path B — Direct Supabase from managed agent

**Credential vault:**
- `SUPABASE_SERVICE_ROLE_KEY` = Supabase → Project Settings → API → service_role

**Environment variable (plain, not secret):**
- `SUPABASE_URL` = `https://cipkcdlsgvyvqklagycu.supabase.co`

Use skill Step 4B only; Step 4A bridge still preferred when both are available.

## Verification

```sql
select source_url, impact_tag, estimated_priority, ingested_at
from rd_insights
order by ingested_at desc
limit 10;
```

Bridge smoke test (from your machine, replace token):

```bash
curl -s -X POST https://omerion-build-production.up.railway.app/internal/rd/insights \
  -H "Authorization: Bearer YOUR_OMERION_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "run_date": "2026-06-26 UTC",
    "rows": [{
      "source_url": "https://omerion.test/bridge-smoke",
      "source_type": "rss",
      "title": "Bridge smoke test",
      "summary": "Delete me.",
      "impact_tag": "internal_os",
      "estimated_priority": "low",
      "raw_content": "probe",
      "metadata": {"probe": true}
    }]
  }'
```

Expected: `{"supabase_upserts":1,"duplicates_dropped":0,"errors":[]}`

## Skill ID

`skill_01DLjFz6eQ5ViD6HvS11EWWs` — `r1-market-tech-watcher` only; no other skills.
