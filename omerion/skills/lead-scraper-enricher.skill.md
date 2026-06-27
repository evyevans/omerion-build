---
name: lead_scraper_enricher
version: 2.0.0
tier: A
agent_number: 3
graph: agents.lead_scraper_enricher.graph:build
triggers:
  - event:account.batch.ready     # MAP emits when qualifying accounts are discovered
  - discord                       # reactive: founder posts in #scout
events_consumed:
  - account.batch.ready           # carries {account_ids, market_segment}
events_emitted:
  - contact.enriched              # downstream: RATE (icp-scoring) consumes this
hitl: true                        # G1 gate — founder reviews enriched contacts before persistence
model_tier: DEFAULT               # Claude Sonnet for persona classification + relevance judgment
discord_channel: scout
rate_limits:
  - hunter                        # Hunter.io — $49/mo plan = 500 verifications/month
  - linkedin
  - anthropic
concurrency:
  lock: pg_advisory_lock
  key: account_domain
owns_tables:
  - contacts                      # write — one row per enriched contact (upsert on email)
reads_tables:
  - accounts                      # source accounts with domains to scrape
  - account_dossiers              # SOURCE's dossier output for research context
---

# FIND — Lead Scraper & Enricher (Agent #3)

## Identity & Scope
FIND is Omerion's contact-level intelligence extractor. Given qualified accounts
(domains) with their dossiers from SOURCE, FIND identifies the *specific humans*
at each company who hold decision-making authority, classifies their persona,
verifies their email, scores their contact priority, and writes structured
contact records into the `contacts` table.

FIND is the bridge between company-level intelligence (SOURCE) and
individual-level scoring (RATE). Its output quality directly determines whether
outreach messages reach the right person at the right time with the right angle.

- **You DO:** Scrape company team/about pages, identify decision-makers, classify
  personas, verify emails via Hunter.io, and output structured contact records.
- **You DO NOT:** Perform deep company research (SOURCE). Score contacts (RATE).
  Send outreach (REACH/GROW). Modify the CRM directly.

## Omerion ICP Context — Who FIND Is Looking For

### Target Persona Hierarchy
FIND must identify and prioritize contacts in this strict order. If a company
has multiple matches, the **highest-priority** contact wins the primary slot:

| Priority | Title Pattern | Persona Tag | Why |
|----------|---------------|-------------|-----|
| 1 (highest) | CEO, Founder, Owner, President, Managing Partner | `sme_founder` | Direct buyer authority. Signs the SOW. |
| 2 | COO, VP Operations, Head of Ops, Director of Operations | `ops_leader` | Operational pain owner. Champions automation. |
| 3 | CRO, VP Sales, Head of Revenue, Sales Director | `revenue_leader` | Revenue pressure = urgent buyer. |
| 4 | CFO, VP Finance, Head of Finance, Controller | `finance_ops` | Cost reduction buyer. Longer sales cycle but high ACV. |
| 5 | VP People, Head of Talent, HR Director | `hr_talent_leader` | Secondary buyer. Only if no higher-priority match. |
| 6 | CTO, VP Engineering, Head of Technology | `tech_leader` | Evaluator, not buyer. Include only for tech-forward SMBs. |

**Hard rule:** If a company has > 200 employees, deprioritize `sme_founder` — at
that size the COO/VP Ops is a more reliable entry point than a CEO who delegates.

### Email Verification Standards
- **Verified (Hunter score ≥ 90):** proceed to RATE with full confidence.
- **Probable (Hunter score 50–89):** proceed with `email_confidence = "probable"`.
  REACH will use LinkedIn first, email as fallback.
- **Risky (Hunter score < 50 OR catch-all domain):** persist contact but set
  `email_confidence = "risky"`. GROW will skip direct email. Flag for manual check.
- **No email found:** persist contact with `email = null`. Still valuable for
  LinkedIn-only outreach. Set `email_confidence = "none"`.

### Hunter.io Budget Discipline
The $49/month plan gives 500 verifications. At ~20 accounts/day, each averaging
3 contacts, FIND would exhaust the monthly budget in 8 days. Budget enforcement:
- **Verify only Priority 1–3 contacts** (founder, ops, revenue). Priority 4–6
  get `email_confidence = "unverified"` and skip Hunter.
- **Track `state.hunter_budget_used`** per run. If monthly usage exceeds 400/500,
  switch to LinkedIn-only enrichment and alert founder.

## Trigger & Input Contract
- **Primary event:** `account.batch.ready` from MAP (Agent #1). Carries
  `{account_ids, market_segment}`. FIND loads the corresponding accounts from
  the `accounts` table and their dossiers from `account_dossiers`.
- **Reactive:** founder posts in `#scout` (e.g., "enrich contacts for acmecorp.com").
  Parsed to a single domain override.
- **Input state:**
  ```
  FindState {
    account_ids: list[UUID],        # from event payload
    market_segment: str | None,
    accounts: list[Account],        # loaded at Node 1
    dossiers: dict[str, Dossier],   # loaded at Node 1
    contacts: list[Contact],        # populated by Node 3
    hunter_budget_used: int,        # running monthly count
    decision: str | None,           # from HITL
  }
  ```

## Reasoning Chain (9-node LangGraph graph)

```
load_accounts
  → scrape_team_pages
  → extract_contacts         (Claude Sonnet — structured extraction)
  → classify_persona          (Claude Sonnet — persona tagging)
  → verify_emails             (Hunter.io — budget-gated)
  → rag_dedup                 (Pinecone — skip known contacts)
  → hitl_review               (G1 gate — founder reviews batch)
  → hitl_wait                 ← interrupt(); PostgresSaver checkpoints
  → persist_and_emit          (write contacts + emit contact.enriched)
```

### Node 1 — `load_accounts`
- **Purpose:** Hydrate state with account data and their SOURCE dossiers.
- **Query:** `accounts` where `id IN (state.account_ids)` AND
  `status IN ("researched", "needs_contacts")`.
- **For each account:** load `account_dossiers` by domain to get pain signals,
  recommended service package, and research context.
- **Output:** `state.accounts`, `state.dossiers`
- **Failure mode:** Supabase error → exception propagates. Run fails, retried
  on next trigger.

### Node 2 — `scrape_team_pages`
- **Purpose:** Gather raw HTML/text from pages likely to contain team member data.
- **Per-account scrape sequence (prioritized):**
  1. `fetch_page(domain + "/about")` — most common location for team bios.
  2. `fetch_page(domain + "/team")` — alternative team page.
  3. `fetch_page(domain + "/leadership")` — enterprise-style org pages.
  4. `scrape_linkedin_page(linkedin_url + "/people")` — LinkedIn people tab.
- **Budget:** maximum **4 tool calls per account**. Stop as soon as at least one
  page returns team member data.
- **Output:** `state.raw_team_data[domain]` (dict of source → raw content)
- **Failure mode:** per-tool errors are caught. If no page returns usable content,
  mark account `enrichment_status = "no_team_data"` and skip. Log
  `find_no_team_page`.

### Node 3 — `extract_contacts`
- **Purpose:** LLM-based structured extraction from raw team page content.
- **Tool:** `extract_contacts_from_page(router, raw_team_data, dossier)` →
  Tier.DEFAULT (Sonnet), `max_tokens=800`, `temperature=0.1`
- **System prompt:** `EXTRACT_SYSTEM` instructs Sonnet to:
  1. Identify all named individuals with professional titles.
  2. Extract `full_name`, `title`, `bio_snippet`, `linkedin_url` (if present).
  3. **NEVER fabricate names or titles.** If ambiguous, set `confidence = "low"`.
  4. Return strict JSON array of `RawContact` objects.
- **Output:** `state.raw_contacts[domain]` (list of RawContact per account)
- **Failure mode:** Parse error → log `find_extract_failed`. Skip account. Continue.

### Node 4 — `classify_persona`
- **Purpose:** Map each raw contact's title to an Omerion persona tag using the
  seniority hierarchy above.
- **Tool:** `classify_persona(router, raw_contact, company_size)` →
  Tier.DEFAULT (Sonnet), `max_tokens=200`, `temperature=0.0`
- **Classification rules (deterministic first, LLM fallback):**
  - Exact title match against the hierarchy table → persona assigned, no LLM needed.
  - Ambiguous titles (e.g., "Director of Growth", "Head of Digital") → Sonnet
    classifies with rationale.
  - Unclassifiable → `persona = "NEEDS_REVIEW"`, `priority = 6`.
- **Company size adjustment:** if `estimated_size > 200`, deprioritize `sme_founder`
  to Priority 3 (COO/VP Ops becomes the target).
- **Output:** `state.contacts` with `persona`, `priority`, `classification_rationale`

### Node 5 — `verify_emails`
- **Purpose:** Find and verify email addresses via Hunter.io.
- **Budget gate:** check `state.hunter_budget_used`. If monthly usage > 400,
  skip Hunter entirely. Set `email_confidence = "budget_exceeded"`.
- **Per-contact (Priority 1–3 only):**
  1. `hunter_email_finder(domain, full_name)` → returns `email`, `score`, `type`.
  2. If `score >= 90` → `email_confidence = "verified"`.
  3. If `score 50–89` → `email_confidence = "probable"`.
  4. If `score < 50` or catch-all → `email_confidence = "risky"`.
  5. If no result → `email = null`, `email_confidence = "none"`.
- **Priority 4–6 contacts:** skip Hunter. Set `email_confidence = "unverified"`.
- **Output:** `state.contacts` with `email`, `email_confidence`, `email_source`
- **Failure mode:** Hunter 429 → backoff `[4, 15, 60]`. After 3 retries, set
  `email_confidence = "hunter_unavailable"` and continue.

### Node 6 — `rag_dedup`
- **Purpose:** Check Pinecone `contacts` namespace for already-enriched contacts.
  Prevent duplicate records and wasted Hunter verifications.
- **Per-contact:** `query_contact_history(email_hash | name_hash)` → if existing
  contact with `enriched_at < 30 days` AND `email_confidence ∈ ("verified", "probable")`,
  mark as `dedup_skipped = true` and copy prior data.
- **Output:** `state.contacts_to_persist` (minus deduped), `state.dedup_skipped`
- **Failure mode:** Pinecone unavailable → skip dedup, all contacts proceed.
  Log `find_rag_dedup_failed`.

### Node 7 — `hitl_review` + `hitl_wait`
- **Purpose:** Build the contact review card for founder approval.
- **Card contents:** per-account grouping showing: domain, dossier confidence,
  contact count, per-contact: name, title, persona, priority, email, confidence.
  Highlighted: any `NEEDS_REVIEW` personas, any `risky` emails, Hunter budget
  usage this month.
- **Replay guard:** returns early if `state.decision in ("approved", "rejected")`.
- **Output:** `state.decision`

### Node 8 — `persist_and_emit`
- **Purpose:** Write approved contacts to Supabase, embed in Pinecone, emit events.
- **Per-contact:**
  1. `upsert_contact(contact)` → `contacts` table, idempotency key `(email)` or
     `(full_name, domain)` if no email.
  2. `update_account_status(domain, "contacts_enriched")` → `accounts.status`.
  3. `embed_contact(contact)` → Pinecone `contacts` namespace for RAG dedup.
- **Events emitted:** one `contact.enriched` event per contact with:
  `{contact_id, account_id, domain, persona, priority, email_confidence,
  recommended_service_package}`.
- **Skips:** if `state.decision != "approved"` or `state.contacts_to_persist` is empty.
- **Output:** `state.contacts_persisted`, `state.contacts_skipped`

## Output Contract

### Per Contact (Supabase `contacts` table row):
```json
{
  "contact_id": "uuid",
  "account_id": "uuid",
  "domain": "acmecorp.com",
  "full_name": "Sarah Chen",
  "title": "VP of Operations",
  "persona": "ops_leader",
  "priority": 2,
  "bio_snippet": "15 years in ops leadership. Previously at Shopify.",
  "linkedin_url": "https://linkedin.com/in/sarah-chen-ops",
  "email": "sarah@acmecorp.com",
  "email_confidence": "verified",
  "email_source": "hunter",
  "hunter_score": 95,
  "classification_rationale": "Title 'VP of Operations' matches ops_leader pattern exactly.",
  "source_pages": ["https://acmecorp.com/about", "https://linkedin.com/company/acme-corp/people"],
  "enriched_at": "2026-06-03T07:30:00Z",
  "status": "enriched"
}
```

### Golden Multi-Contact Output

A realistic 3-contact enrichment from one account:

```json
[
  {
    "full_name": "Marcus Rivera",
    "title": "CEO & Founder",
    "persona": "sme_founder",
    "priority": 1,
    "email": "marcus@acmecorp.com",
    "email_confidence": "verified",
    "hunter_score": 97,
    "classification_rationale": "Title 'CEO & Founder' matches sme_founder pattern. Company is 75 employees — SMB sweet spot, founder still actively involved."
  },
  {
    "full_name": "Sarah Chen",
    "title": "VP of Operations",
    "persona": "ops_leader",
    "priority": 2,
    "email": "sarah@acmecorp.com",
    "email_confidence": "probable",
    "hunter_score": 72,
    "classification_rationale": "Title 'VP of Operations' matches ops_leader exactly. Secondary contact — recommended entry point if CEO does not respond within 7 days."
  },
  {
    "full_name": "Alex Kim",
    "title": "Head of Analytics",
    "persona": "NEEDS_REVIEW",
    "priority": 6,
    "email": null,
    "email_confidence": "none",
    "hunter_score": null,
    "classification_rationale": "Title 'Head of Analytics' does not match any standard persona. May be a data leader or a tech evaluator. Flagged for founder classification."
  }
]
```

## Guardrails
1. **NEVER fabricate contact names, titles, or emails.** Every field must come
   from scraped source data. If a name is ambiguous, set `confidence = "low"`.
2. **NEVER classify a contact as `sme_founder` for a company with > 200 employees**
   unless the founder is still visibly active in operations (LinkedIn posts,
   conference talks).
3. **NEVER exceed the Hunter.io monthly budget.** Switch to LinkedIn-only
   enrichment when budget hits 400/500. Alert founder.
4. **NEVER persist a contact without at least `full_name + title + domain`.**
   Email is optional; identity is not.
5. **ONE primary contact per account.** Always the highest-priority persona.
   Secondary contacts are enriched but marked `is_primary = false`.

## Stop Conditions

| Condition | Behavior |
|-----------|----------|
| Zero accounts loaded | Run completes normally. No HITL card. Log `find_no_accounts`. |
| All accounts have `enrichment_status = "no_team_data"` | Run completes. Log `find_all_no_team_data`. HITL card shows zero contacts. |
| Hunter.io budget exceeded (400+ verifications this month) | Continue enrichment without verification. Set `email_confidence = "budget_exceeded"` for all remaining. Alert founder. |
| LLM extraction fails for all accounts | Run completes with zero contacts. Log `find_all_extract_failed`. No HITL card (nothing to review). |
| Founder rejects batch | `persist_and_emit` returns without writing. Log `find_batch_rejected`. |

## Idempotency Rules
- `contacts` table upserts on `(email)` — re-running FIND for the same domain
  safely overwrites prior contact data with fresher enrichment.
- For contacts without email: upsert on `(full_name, domain)` composite key.
- Pinecone `contacts` namespace uses `contact:{email_hash}` as vector ID — safe
  to re-embed.
- `contact.enriched` events use natural key `contact.enriched:{contact_id}` for
  dedup within the broker's dedup window.
- `pg_advisory_lock` on `account_domain` prevents concurrent FIND runs from
  double-processing the same account.

## Fallback Protocol

| Failure | Fallback |
|---------|----------|
| Team/about page returns 404 | Try `/leadership`, `/people`, `/our-team` (3 fallback paths). If all fail, use LinkedIn people tab. |
| LinkedIn people tab blocked (429) | Use `search_web(company_name + " team members")` for third-party team listings. |
| All team page sources fail for an account | Mark `enrichment_status = "no_team_data"`. Skip to next account. Log `find_no_team_page`. |
| Sonnet extraction returns unparseable JSON | Re-prompt once with schema reminder. If second attempt fails, skip account. Log `find_extract_parse_failed`. |
| Hunter.io `email_finder` returns 429 | Apply backoff `[4, 15, 60]` seconds. After 3 retries, set `email_confidence = "hunter_unavailable"`. Continue. |
| Hunter.io `email_finder` returns no result | Set `email = null`, `email_confidence = "none"`. Contact is still valid for LinkedIn outreach. |
| Anthropic API unavailable | ClaudeRouter retries with backoff `[4, 15, 60]`. After 3 failures, skip LLM classification. Use deterministic title-match only (Priority 1–3 exact matches). |
| Supabase persist fails | Log `find_persist_failed` with contact details. Skip that contact. Cron retries on next trigger. |
| Pinecone embed fails | Contact is still written to Supabase. Embedding retried on next run via upsert. |

## Model Tier Rationale
**Claude Sonnet (Tier.DEFAULT) for extract + classify:** Contact extraction from
unstructured HTML requires understanding varied page layouts (WordPress bios,
LinkedIn cards, custom team grids) and reliably outputting structured JSON. Persona
classification for ambiguous titles ("Director of Growth", "Head of Digital
Transformation") requires business-context reasoning that Haiku handles unreliably.
Opus is unnecessary — extraction is a structured task, not open-ended synthesis.

**Deterministic pre-pass for exact title matches:** Priority 1–3 exact matches
(CEO, COO, CRO patterns) are caught by regex before the LLM is called. This
saves ~40% of classification tokens on typical runs.

## Observability
- **Langfuse trace prefix:** `find.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `contacts_enriched` per run (target: ≥ 15 when accounts exist)
  - `persona_distribution` — breakdown by persona tag per run (healthy = mostly
    sme_founder + ops_leader + revenue_leader)
  - `needs_review_rate` — % of contacts tagged `NEEDS_REVIEW` (rising rate means
    title patterns need expansion)
  - `email_verified_rate` — % of Priority 1–3 with `email_confidence = "verified"`
    (target: ≥ 60%)
  - `hunter_budget_used` — monthly cumulative (alert at 400/500)
  - `dedup_hit_rate` — how often Pinecone catches already-enriched contacts
  - `no_team_data_rate` — % of accounts with zero team data (rising rate means
    scraping tools are degrading)
  - `avg_contacts_per_account` — target: 1.5–3.0

## Config Reference
All runtime config under `config/agents.yaml → lead_scraper_enricher`:

| Key | Purpose | Default |
|-----|---------|---------|
| `max_accounts_per_run` | Cap on accounts processed per run | 20 |
| `max_contacts_per_account` | Max contacts to extract per account | 5 |
| `hunter_monthly_budget` | Monthly Hunter.io verification budget | 500 |
| `hunter_budget_alert_threshold` | Monthly usage count that triggers founder alert | 400 |
| `verify_priorities` | Which priority levels get Hunter verification | `[1, 2, 3]` |
| `dedup_window_days` | Days before a contact is considered stale for dedup | 30 |
| `team_page_fallback_paths` | Alternative paths to try when `/about` returns 404 | `["/team", "/leadership", "/people", "/our-team"]` |
