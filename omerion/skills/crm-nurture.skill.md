---
name: crm-nurture
tier: B
agent_number: 5
graph: agents.crm_nurture.graph:build
triggers:
  - event:contact.scored          # primary: RATE hands off scored contacts
  - discord                       # reactive: founder posts in #nurture
  - cron                          # daily sweep — cadence in config/agents.yaml (crm_nurture.schedule), via the runtime scheduler through the full run lifecycle (NOT skill-frontmatter direct-dispatch)
events_consumed:
  - contact.scored
events_emitted:
  - outreach.email.sent   # EventType.OUTREACH_EMAIL_SENT, one per sent draft
hitl: true                        # G1 outbound-to-humans — founder approves the batch before send
model_tier: DEFAULT                # Sonnet for email drafts
rate_limits:
  - gmail
concurrency:
  lock: pg_advisory_lock
  key: contact_id
---

# GROW — CRM Warm Leads Nurture (Agent #5)

## Identity & Scope
GROW owns the email nurture pipeline for contacts already in the CRM. It runs on
a **daily sweep** (and on `contact.scored` events from RATE, or a `#nurture`
request), drafts a personalized email per due contact, routes the batch through
the founder **G1 outbound gate**, and sends on approval via Gmail. GROW does
**not** write to the `contacts` table (FIND owns enrichment). It does **not**
approve its own sends. It does **not** run LinkedIn outreach (REACH owns that).

## Trigger & Input Contract
- **Event:** `contact.scored` from RATE (Agent #6) pre-populates `state.candidate_contact_ids`; `contact.engagement` escalates a hot contact for an immediate re-touch.
- **Reactive:** founder posts in `#nurture` (e.g. "draft an email for Jane saying X") — parsed to a target contact + custom instructions.
- **Cron:** daily sweep (`config/agents.yaml → crm_nurture.schedule`, default `0 13 * * *`), registered in `scheduler.py` through the full run lifecycle.
- **Input:** `contacts` in stages `[new_lead, contacted, engaged, proposal_sent]`, updated within 14 days (or specific `candidate_contact_ids`).
- **Stop conditions enforced at load time** (`crm_nurture.stop_conditions`): `do_not_contact`, `replied`, `explicit_no`, `signed_agreement`, `meeting_booked`.

## Reasoning Chain (LangGraph edges; deterministic gates + single-shot draft)

Cooldown/stop-condition gating stays deterministic (hard business rules — not
model discretion). The draft is one style-guarded, persona-mapped, RAG-augmented
Sonnet call. LangGraph owns the edges + the G1 interrupt.

```
parse_discord_intent        (reactive only; resolves the named contact — never fabricates one)
  → load_candidates         (due cohort; stop-conditions filtered at load)
  → filter_due              (cooldown gating + engagement-escalation override)
  → rag_augment             (recall winning angle from Pinecone outreach_signals)
  → draft                   (Sonnet email per candidate; rag_context injected)
  → hitl_gate               (G1 outbound — founder approves the batch; interrupt())
  → send_or_discard         (Gmail send on approval; advisory-locked; NO-OP if rejected)
  → emit                    (OUTREACH_EMAIL_SENT per sent message)
  → write_signals           (write outcome vectors to Pinecone + upsert threads)
```

### Node — `hitl_gate` (TWATR HITL, gate G1)
- Founder approves the **whole batch** before any send — one card listing every draft (to · template · subject · body).
- Routed through the global policy `omerion_core/hitl/policy.py::gate(Gate.OUTBOUND_TO_HUMANS, …)` → single `interrupt()`. Sets `state.decision`; fail-closed (no approval → `send_or_discard` is a no-op).

### Node 1 — `load_candidates`
- **Purpose:** Pull contacts eligible for nurture from Supabase.
- **Tools called:** `load_candidates(contact_ids | None)`
- **Query:** `contacts` JOIN `accounts`, filtered to `stage IN (new_lead, contacted, engaged, proposal_sent)` AND `updated_at >= 14 days ago`. When `candidate_contact_ids` is set (event-triggered), uses `.in_()` filter instead.
- **Stop condition filter:** rows with any stop-condition flag set are dropped before returning.
- **Output:** `state.candidates` (list of `NurtureCandidate`)
- **Failure mode:** Supabase query errors → exception propagates, run fails. Cron will retry next hour.

### Node 2 — `filter_due`
- **Purpose:** Apply cooldown gating and engagement-escalation override.
- **Tools called:** `needs_touch(candidate)` — reads `cooldown_periods[stage]` from `config/agents.yaml`
- **Cooldown logic:** if `days_since_last_touch < cooldown_for_stage`, check engagement score;
  if engagement score ≥ `(opens_in_24h × 1.0 + link_clicks × 3.0) / 2.0`, bypass cooldown.
  Otherwise skip.
- **Output:** `state.candidates` (filtered list), `state.skipped_cooldown` counter incremented
- **Failure mode:** malformed `cooldown_periods` YAML returns `99` days — contacts are effectively frozen. Monitor `skipped_cooldown` for anomalies.

### Node 3 — `rag_augment`
- **Purpose:** Query `outreach_signals` Pinecone namespace for successful past messaging patterns.
- **Tools called:** `query_outreach_signals(persona, stage)` per candidate
- **Output:** `candidate.rag_context` — injected into each `NurtureCandidate`; passed verbatim to draft prompts so the model can reference what angles worked for this persona/stage combination.
- **Failure mode:** Pinecone unavailable → `rag_context` stays empty string. Draft quality degrades but run continues.

### Node 4 — `draft`
- **Purpose:** Generate one email per candidate.
- **Model tier:** `draft_email()` → `Tier.DEFAULT` (Sonnet), `max_tokens=600`, `temperature=0.4`
- **Template key format:** `email_{stage}_v1` (e.g. `email_contacted_v1`)
- **Output:** `state.drafts` (list of `NurtureDraft`); candidates with no valid contact address are counted in `state.skipped_stop_condition`
- **Failure mode:** LLM error per candidate → exception propagates up from `draft_for`; the entire batch node fails. Individual draft errors should be caught — this is a known gap.

### Node 5 — `hitl_gate` (G1)
- See the `hitl_gate` description above — one batch card via the global HITL policy,
  single `interrupt()`, `state.decision` set, fail-closed. Replaces the former
  `hitl_review` + `hitl_wait` pair and the hand-rolled `create_founder_review_task`.

### Node 6 — `send_or_discard`
- **Purpose:** Deliver approved drafts via Gmail.
- **Per-draft sequence:**
  1. Acquire `pg_advisory_lock(contact_id)` — skip if locked (concurrent run owns this contact)
  2. Call `deliver(draft, candidate)` → `send_email()`
  3. On success: `log_outbound(draft, candidate, provider_id)` → upsert to `outbound_communications`, insert to `contact_activity_log`
  4. On delivery exception: log error, mark `draft.approved = False`, increment `state.failed_count`, continue to next draft
- **Rate limiting:** `time.sleep(0.5)` between sends — this is a **synchronous sleep in an async graph** and will block the event loop on high volumes. Known issue; needs async replacement.
- **Advisory lock fail-closed:** if `pg_advisory_lock` RPC is unavailable, lock returns `False` and the contact is skipped for this run. Prevents duplicate sends under Supabase degradation.
- **Output:** `state.sent_count`, `state.failed_count`

### Node 7 — `emit`
- **Purpose:** Publish `OUTREACH_EMAIL_SENT` per delivered message.
- **Skips node:** if `state.decision != "approved"`

### Node 8 — `write_signals`
- **Purpose:** Write interaction vectors to Pinecone `outreach_signals` namespace and upsert `outreach_threads` row.
- **Tools called:** `write_outreach_signal(...)`, `upsert_outreach_thread(contact_id, channel)` from `omerion_core/outreach/signals`
- **Output:** `state.rag_signals_written` counter
- **Skips node:** if `state.decision != "approved"`

## Output Contract
- **Supabase `outbound_communications`:** upsert keyed on `idempotency_key` = UUID5 of `{contact_id}:{template_key}:{date}` — duplicate sends on retry are prevented
- **Supabase `contact_activity_log`:** INSERT (no idempotency) — retry creates duplicate activity rows
- **Pinecone `outreach_signals` namespace:** signal vectors per sent message
- **Events emitted:** `OUTREACH_EMAIL_SENT` per approved draft (EventType enum, never raw strings)
- **State counters logged:** `sent_count`, `failed_count`, `skipped_cooldown`, `skipped_stop_condition`, `rag_signals_written`

## Guardrails
- **NEVER send without HITL approval** — `send_or_discard` checks `state.decision != "approved"` and exits early on rejection.
- **Cooldown enforcement:** one touch per contact per cooldown window (per-stage, from config). Engagement escalation can override cooldown — monitor for abuse.
- **Ghost threshold:** contacts untouched for 21+ days (`global config`) get a ghost follow-up sequence at stage 4–5.
- **Stop conditions are absolute:** `do_not_contact` and `explicit_no` are checked at load time; no amount of scoring or engagement overrides them.
- **Advisory lock scope:** `pg_try_advisory_xact_lock` is transaction-scoped. Two concurrent GROW runs cannot double-touch the same contact within the same transaction window.

## Stop Conditions
- **No candidates after filtering:** all nodes after `filter_due` short-circuit; run completes normally, no HITL card created.
- **No drafts generated:** `hitl_review` and downstream nodes skip.
- **Batch rejected by founder:** `send_or_discard` logs rejection and exits; no messages sent.
- **All sends fail:** `state.sent_count == 0` after `send_or_discard`; run completes, signals not written, events not emitted.

## Idempotency Rules
- `outbound_communications` upsert on `idempotency_key` (UUID5 of contact+template+date) — re-running GROW on the same day for the same contact is safe.
- `contact_activity_log` INSERT has no idempotency — retry creates duplicate activity rows. Low impact (reporting only), but monitor for inflated counts.
- `outreach_signals` Pinecone vectors use stable IDs — safe to re-index.

## Fallback Protocol
- **Gmail API 429:** `deliver()` raises → caught in `send_or_discard`; draft marked failed, run continues. Retry on next cron tick (idempotency key prevents duplicate send).
- **Pinecone down in `rag_augment`:** `rag_context` stays empty, draft quality degrades, run continues.
- **Pinecone down in `write_signals`:** `write_outreach_signal` raises → exception propagates out of `write_signals_node`. Run is effectively complete (sends already went); this is a data-loss scenario for the RAG feedback loop. Should catch and log rather than raise.
- **`config/agents.yaml` malformed cooldown:** `cooldown_periods.get(stage, 99)` returns 99 → all contacts frozen. Monitor `skipped_cooldown` spike.

## Model Tier Rationale
**Sonnet for email drafts** — email drafting is persona-aware, requires nuanced voice matching 6 persona variants across 5 nurture stages. Sonnet at `temperature=0.4` balances quality with consistency across a batch.

## Observability
- **Langfuse trace prefix:** `nurture.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `sent_count` per run (target: ≥ 1 when candidates exist)
  - `failed_count` — any non-zero value needs investigation
  - `skipped_cooldown` per run — spike means contact list is cooling off
  - `stage_distribution` of candidates — leading indicator of pipeline health
  - `hitl_approval_rate` — founder rejection rate; high rate signals prompt quality issue
  - `opportunity_creation_rate` — `opportunity.created` events per `outreach.sent` events over 7 days
  - `rag_signals_written` — confirms Pinecone feedback loop is running

## Config Reference
All runtime config under `config/agents.yaml → crm_nurture`:

| Key | Purpose |
|-----|---------|
| `cooldown_periods` | Per-stage cooldown days (e.g. `contacted: 3`, `engaged: 7`) |
| `stop_conditions` | Field names checked at load time (do_not_contact, etc.) |
| `escalation_threshold` | Engagement score thresholds to bypass cooldown |
| `ghost_threshold` | Days before a stale contact gets ghost follow-up sequence |
| `max_shortlist_size` | Not used by GROW (used by RATE) — ignore |
