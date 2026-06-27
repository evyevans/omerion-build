# Phase 7 — End-to-End Verification Runbook

Run these in order. Each step has a **PASS** signal. Stop on the first failure.

## 0. Prereqs

- `.env` populated from `.env.example` (all required creds — see Phase 0 in the plan)
- Supabase migrations 0001–0011 applied
- `uv pip install -e .` has completed without errors
- `ngrok http 8000` running in a second terminal (for Fireflies webhook smoke test)
- `OMERION_PUBLIC_BASE_URL` set to the ngrok https URL in `.env`

## 1. Boot

```bash
cd omerion
uv run uvicorn main:app --reload --port 8000
```

**PASS**: FastAPI starts, log shows "14 skills scheduled", no tracebacks. Leave this running.

In a second shell:

```bash
curl -s http://localhost:8000/health/services | jq
```

**PASS**: all services report `ok: true` (supabase, anthropic, pinecone, google, github, fireflies).

## 2. Agent SDK sanity — Lead Enricher (#3)

```bash
curl -s -X POST http://localhost:8000/agents/lead_scraper_enricher/run \
  -H "Authorization: Bearer $OMERION_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"contact": {"email":"test@acmegrowth.com","company":"Acme Growth Co","title":"SME Founder"}}'
```

**PASS**: 200; Supabase `contacts` row created with `persona in (sme_founder,...)`.

## 3. LangGraph HITL — Meeting Intelligence (#8)

Send a sample Fireflies webhook:

```bash
curl -s -X POST $OMERION_PUBLIC_BASE_URL/webhooks/fireflies \
  -H "X-Fireflies-Signature: <computed>" \
  -H "Content-Type: application/json" \
  -d @samples/fireflies_discovery_call.json
```

**PASS**:
- `agent_sessions` row with `status='paused'` on `meeting_intelligence`
- `founder_review_queue` row with consulting proposal in `context_md`
- Log shows `hitl_review_created` and Discord webhook fires to #founder-hitl

Resolve the review:

```bash
REVIEW_ID=<id from queue>
curl -s -X POST http://localhost:8000/hitl/resolve \
  -H "Authorization: Bearer $OMERION_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"review_id\":\"$REVIEW_ID\",\"token\":\"<approve_token>\",\"decision\":\"approved\",\"source_channel\":\"sheets\"}"
```

**PASS**: graph resumes; `opportunities` row populated with `service_package` + `demo_reference`; Google Doc URL in logs.

## 4. Managed Agent — R1 Market/Tech Watcher

```bash
curl -s -X POST http://localhost:8000/agents/r1_market_tech_watcher/run \
  -H "Authorization: Bearer $OMERION_WEBHOOK_TOKEN"
```

**PASS**: Anthropic returns a session with the managed-agents beta header; `rd_insights` rows inserted; Pinecone namespace `rd` has new vectors.

## 5. Offer Matching shape

Inspect the `opportunities` row from step 3:

```sql
select service_package, demo_reference, proposal_payload
from opportunities order by created_at desc limit 1;
```

**PASS**: `service_package` ∈ {revenue_acceleration_engine, ops_intelligence_layer, research_decision_stack, process_automation_suite}; `proposal_payload` contains `thirty_sixty_ninety`, `pricing.band`, no `modules` field.

## 6. Build Orchestrator — client mode

```bash
curl -s -X POST http://localhost:8000/agents/build_orchestrator/run \
  -H "Authorization: Bearer $OMERION_WEBHOOK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"blueprint_id":"<from-step-3>","mode":"client","client_slug":"acme-growth"}'
```

Approve the deployment HITL review via the same `/hitl/resolve` pattern.

**PASS**: `clients` row has `drive_folder_id`; Drive has new folder with proposal + sow + blueprint + handoff Google Docs; `deployments.mode='client'`.

## 7. Dashboard

```bash
cd ../dashboard && npm run dev
```

Open `http://localhost:3000`.

**PASS**: Services bar shows 6 real services (Supabase, Claude, OpenAI, Pinecone, Fireflies, Google, GitHub) — no FollowUpBoss / Redis. Live activity stream shows the runs from steps 2–6.

---

## Rollback if any step fails

- Container/process issues → check `logs/omerion.log`; fix env var or restart.
- Schema mismatch → re-run migrations; check `supabase migration list`.
- Prompt output shape wrong → compare to `agents/{name}/_legacy/prompts.py`, adjust.
- Managed Agent 4xx → verify `ANTHROPIC_MANAGED_AGENTS_BETA` header spelled exactly.
