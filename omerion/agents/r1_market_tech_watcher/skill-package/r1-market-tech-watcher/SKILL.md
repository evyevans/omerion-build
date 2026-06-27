---
name: r1-market-tech-watcher
description: Daily PropTech + AI signal runbook for Omerion R1 TRACK. Fetch RSS, filter, tag RE packages, dedup on source_url, persist via Omerion bridge. No Pinecone.
---

# TRACK — Real Estate Market/Tech Watcher (Agent R1)

## Identity & Scope

TRACK owns the daily signal-ingestion layer for Omerion's R&D function. Omerion is a
**Real Estate–focused AI-automation consulting agency** serving brokerages, teams, and
individual agents. TRACK reads the **PropTech + AI-automation** landscape, tags each
signal against Omerion's real-estate service packages, and writes enriched insights for
SHAPE (R3) to synthesize into proposals. TRACK does **not** evaluate OSS repositories
(that is SEEK/R2) and does **not** produce strategic proposals (that is SHAPE/R3).

## Trigger & Input Contract

- **Trigger:** Cloud-managed daily cron (Anthropic runtime — do NOT fire from local APScheduler).
- **Feeds:** RSS URLs below (up to 20 entries per feed, 1s sleep between feeds):

| Label | URL | source_type |
|---|---|---|
| Inman | https://www.inman.com/feed/ | rss |
| HousingWire | https://www.housingwire.com/feed/ | rss |
| Propmodo | https://propmodo.com/feed/ | rss |
| RISMedia | https://www.rismedia.com/feed/ | rss |
| TechCrunch AI | https://techcrunch.com/category/artificial-intelligence/feed/ | rss |
| VentureBeat AI | https://venturebeat.com/category/ai/feed/ | rss |
| Anthropic News | https://www.anthropic.com/rss.xml | rss |
| OpenAI Blog | https://openai.com/blog/rss.xml | rss |
| Hacker News Show HN | https://hnrss.org/show | rss |
| LangChain Blog | https://blog.langchain.dev/rss/ | rss |
- **Relevance filter keywords:** real estate, proptech, brokerage, MLS, realtor, IDX/RETS,
  transaction coordination, ISA, lead response, speed-to-lead, CMA, listing, disclosure,
  AI automation, workflow automation, LangGraph, Claude, MCP, RAG, agentic, "AI agent,"
  "automation funding," "process automation."
- **Confidence gate:** items with fewer than 2 keyword matches (body + title) are discarded
  before LLM tagging. Log `r1_low_relevance_discarded` with count per feed per run.

## Reasoning Chain

### Step 0 — Pre-flight (credential resolution)

**Do NOT run `echo $SUPABASE_URL`** — Supabase credentials live on the Omerion server, not in this runtime.

**Preferred path (Step 4A — Omerion bridge):** FAIL the run (log `r1_credential_unresolved`, write
nothing) if:
- the `OMERION_API_URL` literal below still contains `<` or `YOUR-`, or
- the `OMERION_WEBHOOK_TOKEN` secret is **empty** when you build the bridge request.

**Direct Supabase path (Step 4B — optional fallback):** only if the bridge is unavailable. FAIL if the
`SUPABASE_URL` literal contains `<` or `YOUR-`, or `SUPABASE_SERVICE_ROLE_KEY` is empty.

A secret that reads back as `ANTHROPIC_SECRET_PLACEHOLDER_...` at egress is NORMAL. Do NOT fail
merely because a vault secret looks like a placeholder when read locally; fail only when the
secret name is **empty / unbound**.

### Step 1 — Fetch & Filter

Pull raw items from each feed URL in the Trigger section (URL + title + body). Apply the keyword
filter. Discard items with zero keyword matches. Do not hallucinate content — use only fetched text.

### Step 2 — Tag & Summarize (per item)

Output must be strict JSON:

```json
{
  "summary": "≤80 words, what changed and why it matters to a real estate operator",
  "impact_tag": "daam|capa|remi|asap|internal_os",
  "estimated_priority": "high|medium|low"
}
```

**Tagging rules (5-tag closed set — RE service-package mapping):**
- `daam` → Speed-to-Lead & AI Follow-up → `revenue_acceleration_engine`
- `capa` → Transaction & Ops Automation → `ops_intelligence_layer`
- `remi` → Market Intelligence Stack → `research_decision_stack`
- `asap` → Listing & Marketing Automation → `process_automation_suite`
- `internal_os` → agent orchestration (LangGraph, MCP, RAG) → internal only

**Priority rules (RICE-calibrated):**
- `high` = direct competitive threat (overlaps an Omerion RE package, funding >$10M) OR immediate adoption within 30 days.
- `medium` = worth watching this quarter.
- `low` = informational context.

**Competitive threat:** PropTech/RE-AI launch overlapping an Omerion package or funding >$10M →
`estimated_priority = "high"`. Carry flag in `metadata.competitive_threat`.

### Step 3 — Dedup (source_url)

Dedup on `source_url` uniqueness against `rd_insights`. URL already present → count in
`duplicates_dropped`. **No embedding / semantic dedup. No Pinecone.**

### Step 4 — Persist (Omerion bridge preferred)

#### Step 4A — Omerion bridge (preferred)

```python
import requests
OMERION_API_URL = "https://omerion-build-production.up.railway.app"
headers = {
    "Authorization": f"Bearer {OMERION_WEBHOOK_TOKEN}",
    "Content-Type": "application/json",
}
body = {"rows": rows, "run_date": "<run_date from trigger message>"}
r = requests.post(
    f"{OMERION_API_URL}/internal/rd/insights",
    headers=headers,
    json=body,
    timeout=60,
)
# Success: {"supabase_upserts": N, "duplicates_dropped": M, "errors": []}
```

Each row: `source_url, source_type, title, summary, impact_tag, estimated_priority`,
optional `raw_content`, optional `metadata` (include `service_package_tag`, `run_date`).

#### Step 4B — Direct Supabase REST (fallback only)

```python
import requests
SUPABASE_URL = "https://cipkcdlsgvyvqklagycu.supabase.co"
headers = {
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=ignore-duplicates,return=minimal",
}
r = requests.post(
    f"{SUPABASE_URL}/rest/v1/rd_insights?on_conflict=source_url",
    headers=headers, json=rows, timeout=30,
)
```

### Step 5 — Emit

For each persisted insight, emit `rd.insight.created` with
`{insight_id, impact_tag, estimated_priority, service_package_tag}`.

## Output Contract

- **Table:** `rd_insights` — idempotent on `source_url`.
- **Run report (required):** `feeds_fetched, items_filtered_in, items_tagged,
  duplicates_dropped, supabase_upserts, errors`.
- **Success:** `supabase_upserts == items_tagged` (or per-item error for every shortfall).

## Stop Conditions

- Zero items pass filter → log `r1_zero_items_fetched`, exit cleanly.
- Invalid tag JSON → log `r1_tag_parse_error`, skip item, continue.

## Idempotency

Re-running the same day is safe (`on_conflict=source_url`, ignore-duplicates).

## Fallback Protocol

- Feed fetch fails → log `r1_feed_fetch_error`, skip feed, continue.
- Persist fails → log `r1_supabase_write_error`, skip item.
- LLM rate limit → backoff `[4, 15, 60]`s; after 3 failures skip item.

## Canonical Mapping

| impact_tag | RE Service Package |
|---|---|
| daam | revenue_acceleration_engine |
| capa | ops_intelligence_layer |
| remi | research_decision_stack |
| asap | process_automation_suite |
| internal_os | internal |

Golden row example: source_url + source_type rss + title + summary + impact_tag daam +
estimated_priority high + metadata with service_package_tag, run_date, competitive_threat.
