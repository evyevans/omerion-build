---
name: linkedin-outreach
tier: B
agent_number: 4
graph: agents.linkedin_outreach.graph:build
schedule: "0 9-17 * * 1-5"       # hourly during business hours Mon–Fri
triggers:
  - cron
  - event:contact.scored
events_consumed:
  - contact.scored
events_emitted:
  - outreach.linkedin.sent        # EventType.OUTREACH_LI_SENT, one per queued+approved draft
hitl: true                        # every draft approved before queuing
model_tier: DEFAULT               # Sonnet for all draft generation
rate_limits:
  - linkedin
daily_caps:                       # MUST match config/agents.yaml (the code reads those keys)
  connection_requests: 25         # agents.yaml: daily_connection_limit
  messages: 40                    # agents.yaml: daily_message_limit
---

# REACH — LinkedIn Outreach (Agent #4)

## Identity & Scope
REACH owns LinkedIn-channel outreach sequences for scored contacts. It loads a
cohort of contacts due for a LinkedIn touch, classifies each into a cold or warm
track, enforces daily platform caps, drafts persona-specific messages via Claude
Sonnet, submits the batch for founder approval, and queues approved messages for
the LinkedIn sender. REACH does **not** email or SMS contacts (GROW owns those
channels). It does **not** approve its own sends. It does **not** import contacts
(FIND owns enrichment).

## Trigger & Input Contract
- **Primary:** hourly cron `0 9-17 * * 1-5` (business hours only)
- **Real-time:** `contact.scored` event can pre-populate `state.candidate_contact_ids`
  so REACH acts on a newly-scored hot/warm contact without waiting for the next cron tick.
- **Input:** contacts eligible for LinkedIn outreach — scored ≥ warm threshold, with a `linkedin_url`, not yet at daily cap.
- **Daily caps enforced at `apply_caps` node** (hard limits from `config/agents.yaml → linkedin_outreach`): **25 connection requests** (`daily_connection_limit`), **40 messages** (`daily_message_limit`) per calendar day across all REACH runs.

## Reasoning Chain (11-node LangGraph graph)

```
load_cohort
  → plan_steps          (cold/warm track + step sequencing)
  → apply_caps          (enforce daily connection/DM platform caps)
  → rag_augment         (inject successful past angles from Pinecone)
  → draft               (Claude Sonnet — one draft per planned step)
  → hitl_review         (founder approves the batch)
  → hitl_wait           ← interrupt(); PostgresSaver checkpoints here (replay-guarded)
  → send_or_discard     (queue approved drafts → outbound_communications)
  → send_messages       (browser-use/Playwright drains the queue → LIVE LinkedIn DMs)
  → emit                (one OUTREACH_LI_SENT per queued draft)
  → write_signals       (index interaction vectors to Pinecone + upsert threads)
```

### Node 1 — `load_cohort`
- **Purpose:** Pull contacts eligible for a LinkedIn touch this cycle.
- **Tools called:** `load_cohort(candidate_contact_ids | None)`
- **Output:** `state.cohort` (list of contacts with persona, stage, linkedin_url, sequence position)
- **Failure mode:** Supabase error → exception propagates, run fails. Next cron tick retries.

### Node 2 — `plan_steps`
- **Purpose:** Determine the correct sequence step for each contact and classify track.
- **Tools called:** `plan_steps(cohort)`
- **Tracks:**
  - **Cold track** (new contacts, no prior LinkedIn interaction): `connect_request → intro_dm → value_add_dm → ask_dm`
  - **Warm track** (contacts that accepted connection or replied): starts at `intro_dm` or later
- **6 persona variants** (from `config/agents.yaml → linkedin_outreach.persona_variants`): one template set per persona, covering all four sequence steps.
- **Output:** `state.planned` (list of `PlannedStep` with contact_id, track, step_type, persona, persona_variant, step_id)
- **Contacts in stop conditions** (do_not_contact, explicit_no, meeting_booked) are excluded; `state.skipped_stopped` counter incremented.

### Node 3 — `apply_caps`
- **Purpose:** Enforce LinkedIn daily platform caps before generating any drafts.
- **Tools called:** `apply_daily_caps(planned)`
- **Logic:** queries today's `outbound_communications` for LinkedIn sends, compares against caps. Steps that would exceed the cap are removed.
- **Output:** `state.planned` (capped list), `state.skipped_capped` incremented for removed steps.
- **Note:** caps are enforced per calendar day in system timezone, not per cron run.

### Node 4 — `rag_augment`
- **Purpose:** Query `outreach_signals` Pinecone namespace for successful past messaging angles.
- **Tools called:** `query_outreach_signals(persona, "contacted")` per planned step
- **Output:** `step.rag_context` injected into each `PlannedStep`; passed to draft prompts.
- **Failure mode:** Pinecone unavailable → `rag_context` stays empty; draft quality degrades, run continues.

### Node 5 — `draft`
- **Purpose:** Generate one message draft per planned step.
- **Tools called:** `draft_message(router, step)` → Tier.DEFAULT (Sonnet)
- **Template key format:** `linkedin_{track}_{step_type}_{persona_variant}` (e.g. `linkedin_cold_connect_request_ops_leader`)
- **Output:** `state.drafts` (list of `LinkedInDraft`)
- **Failure mode:** per-step LLM errors propagate out of list comprehension; entire draft node fails. Individual error isolation is a known gap.

### Node 6 — `hitl_review`
- **Purpose:** Build the batch review card and create a `founder_review_queue` row.
- **Tools called:** `create_founder_review_task(...)` from `omerion_core/hitl/review.py`
- **Card includes:** run date, draft count, capped count, stopped count; per-draft contact ID + track + template key + body
- **Output:** `state.review_id`, `state.hitl_review_id`
- **Skips node:** if `state.drafts` is empty

### Node 7 — `hitl_wait`
- **Purpose:** Suspend graph at `langgraph.types.interrupt(...)`. PostgresSaver checkpoints `LinkedInOutreachState`.
- **Replay guard:** returns early if `state.decision in ("approved","rejected")` *before* calling `interrupt()` — so a resumed/replayed run does not re-pause on an already-resolved review. Critical for a G1 sender.
- **Output:** `state.decision` ∈ `{"approved", "rejected"}`, optional `state.scratch["decision_notes"]`

### Node 8 — `send_or_discard`
- **Purpose:** Queue approved drafts for the LinkedIn sender process.
- **Tools called:** `queue_for_sender(draft, sequence_id, sequence_step=0)` → writes to `outbound_communications`; `log_activity(contact_id, comm_id, "linkedin_queued", metadata)`
- **Sequence ID:** one `uuid4()` per approved batch — all drafts in a run share the same `sequence_id`.
- **Idempotency:** `queue_for_sender` uses a deterministic UUID5 keyed on `(contact_id, template_key, sha256(body)[:16], date)` and **ignore_duplicates** upsert. Re-queuing the same body for the same contact+template on the same day is a no-op that *preserves the existing row's status* — so it can never resurrect a `sent` row back to `queued_for_sender` (which would double-send). A genuinely edited draft hashes differently → distinct key → its own row.
- **`log_activity`:** guarded INSERT to `contact_activity_log` (checks existing `comm_id`+`activity_type` first) — retry-safe.
- **Skips node:** if `state.decision != "approved"`.
- **Output:** `state.sent_count`

### Node 9 — `send_messages`
- **Purpose:** Actually deliver the queued DMs. Drains `outbound_communications` rows with `status='queued_for_sender'` via **browser-use + Playwright** (`send_queued_messages(limit=state.sent_count)`), navigating to each profile and sending the message.
- **This is the live-send node** — the only place a real LinkedIn DM leaves the system. Runs only when `state.decision == "approved"` and `state.sent_count > 0`.
- **Per-message:** updates the row to `sent` / `blocked` / `failed`; logs `linkedin_sent` activity on success. Already-`sent`/`blocked`/`failed` rows are never re-fetched, so the drain is idempotent.
- **Failure isolation:** a sender exception is caught and logged (`li_playwright_drain_failed`) without failing the run — the queued rows remain for a later drain.

### Node 10 — `emit`
- **Purpose:** Publish `OUTREACH_LI_SENT` per queued draft.
- **Skips node:** if `state.decision != "approved"` or `state.sent_count == 0`

### Node 11 — `write_signals`
- **Purpose:** Index interaction vectors to Pinecone `outreach_signals` namespace and upsert `outreach_threads` row.
- **Tools called:** `write_outreach_signal(persona, stage="contacted", channel="linkedin_dm", template_key, angle=persona_variant, ...)`, `upsert_outreach_thread(contact_id, "linkedin")`
- **Output:** `state.rag_signals_written`
- **Skips node:** if `state.decision != "approved"`

## Output Contract
- **Supabase `outbound_communications`:** ignore_duplicates upsert keyed on UUID5 of `(contact_id, template_key, sha256(body)[:16], date)` — retry-safe and status-preserving (never resurrects a `sent` row)
- **Supabase `contact_activity_log`:** guarded INSERT (dedupes on `comm_id`+`activity_type`) — retry-safe
- **Pinecone `outreach_signals` namespace:** signal vectors per sent draft
- **Events emitted:** `OUTREACH_LI_SENT` (`outreach.linkedin.sent`) per queued draft
- **State counters logged:** `sent_count`, `skipped_capped`, `skipped_stopped`, `rag_signals_written`

## Guardrails
- **NEVER exceed daily caps.** `apply_caps` queries today's send count before drafting. Hard ceiling at the node level, not just config.
- **NEVER send without HITL approval.** `send_or_discard` checks `state.decision != "approved"` and returns early on rejection.
- **NEVER draft for stopped contacts.** `do_not_contact`, `explicit_no`, `meeting_booked` contacts are filtered at `plan_steps` before any LLM call.
- **Sequence step ordering.** Cold track always starts at `connect_request`. Warm track starts at the next uncompleted step. Never skip steps.

## Stop Conditions
- **No cohort loaded:** all downstream nodes short-circuit.
- **All steps capped:** `state.planned` empty after `apply_caps`; no drafts, no HITL, run completes cleanly.
- **Batch rejected by founder:** `send_or_discard` logs and returns; nothing queued.

## Idempotency Rules
- `outbound_communications` **ignore_duplicates** upsert on UUID5 of `(contact_id, template_key, sha256(body)[:16], date)` — re-queueing the same body on the same day is a no-op that preserves the existing row's status (cannot reset a `sent` row to `queued_for_sender`, so no double-send). An *edited* draft hashes to a new key → its own row.
- `contact_activity_log` INSERT is guarded on `(comm_id, activity_type)` — retry-safe, no duplicate activity rows.
- Daily cap query is point-in-time; two concurrent REACH runs can both pass the cap check. The mutex (`mutex_ttl_seconds=1800`, scope `agent.linkedin-outreach.*`) serializes runs, and the LinkedIn platform hard limit is the final backstop.

## Fallback Protocol
- **Pinecone unavailable in `rag_augment`:** `rag_context` = empty; drafts generated without historical context.
- **Sonnet draft error:** draft node fails. Individual per-step error isolation is a known gap.
- **`queue_for_sender` Supabase error:** exception raised; `sent_count` stays at partial count. Idempotency key means re-run only queues un-sent drafts.
- **HITL timeout:** Discord reminder fires from `omerion_core/notifications/hitl.py`. Run stays suspended; does not auto-reject.

## Model Tier Rationale
**Sonnet for all drafts** — LinkedIn outreach requires persona-aware, professional voice with 6 persona variants across 4 sequence step types. Each draft must reference specific signals (title, company, market, pain signal, prior interaction context). Haiku produces noticeably generic copy that reduces connection acceptance rates.

**No escalation to Opus** — LinkedIn messages are brief (≤300 words DMs, ≤280 chars connection notes). Creativity requirements don't justify Opus cost.

## Observability
- **Langfuse trace prefix:** `reach.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `connections_sent` per day (track vs. daily cap of 25)
  - `messages_sent` per day (track vs. daily cap of 40)
  - `acceptance_rate` = connection requests accepted / sent (target: >25%)
  - `reply_rate` = DM replies / DMs sent (target: >8%)
  - `hitl_approval_rate` — founder approval rate per batch
  - `skipped_capped` per run — rising count means cadence outpacing cap budget
  - `rag_signals_written` per week — confirms Pinecone feedback loop is running

## Config Reference
All runtime config under `config/agents.yaml → linkedin_outreach`:

| Key | Purpose |
|-----|---------|
| `daily_caps.connection_requests` | Max connection requests per calendar day (default: 20) |
| `daily_caps.messages` | Max DMs per calendar day (default: 60) |
| `warm_contact_threshold` | Minimum ICP score to include contact in cohort |
| `persona_variants` | 6 persona variant IDs used for template key construction |
| `cold_sequence` | Ordered step list for cold track |
| `warm_sequence` | Ordered step list for warm track |
| `personalization_fields` | Contact + account fields injected into draft prompts |
