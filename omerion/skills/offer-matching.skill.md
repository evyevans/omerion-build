---
name: offer-matching
tier: C
agent_number: 7
graph: agents.offer_matching.graph:build
triggers:
  - event:contact.scored          # RATE (#6) emits CONTACT_SCORED per scored contact
events_consumed:
  - contact.scored
events_emitted:
  - proposal.ready                # PROPOSAL_READY — one per approved batch
  - proposal.draft.ready          # PROPOSAL_DRAFT_READY — high-confidence (≥0.75) → build-orchestrator
hitl: true
model_tier: OPUS                   # Tier.HEAVY — proposal + 30/60/90 synthesis; must be persuasive enough to send unchanged
rate_limits:
  - anthropic
---

# PAIR — Offer Matching & Playbook (Agent #7)

## Identity & Scope
PAIR owns service package selection and consulting proposal authorship for hot
contacts. It loads contacts scored as `hot` by RATE, synthesizes a matched
service package + demo reference + 30/60/90 delivery plan + founder memo using
Claude Opus, routes the batch for founder approval, then writes approved proposals
to `opportunities` and `generated_drafts`. PAIR does **not** run outreach (REACH
and GROW own those channels). It does **not** approve its own proposals. It does
**not** select packages for warm or watchlist contacts — hot segment only.

## Trigger & Input Contract
- **Trigger:** `contact.scored` event. RATE (#6) emits `CONTACT_SCORED` for *every* scored contact (all segments), and `event_ingress` maps the scalar `contact_id` into `candidate_contact_ids=[contact_id]`. `load_hot_contacts` then filters strictly to `segment = "hot"`, so a non-hot trigger no-ops (empty `hot_contacts` → `propose`/HITL/persist all skip, run completes clean with no LLM, no writes). Net effect: PAIR only does real work on hot contacts; non-hot scores produce a cheap empty run.
  - *(Note: the older `opportunity.created` trigger was never wired — that EventType does not exist in the enum; see `broker.py` comment. Removed to stop documenting a dead trigger.)*
- **Input:** `scores` table rows with `segment = "hot"`, joined with `contacts` and `accounts`. Deduped by `contact_id`, limited to 25 per run.
- **No cron schedule** — PAIR is event-driven. Proposals are generated in response to real signals, not on a fixed cadence.

## Reasoning Chain (6-node LangGraph graph)

```
load_hot_contacts
  → propose             (Claude Opus — one proposal per hot contact)
  → hitl_review         (founder approves/rejects the proposal batch)
  → hitl_wait           ← interrupt(); PostgresSaver checkpoints here
  → persist             (write approved proposals to opportunities + memo drafts)
  → emit                (one PROPOSAL_READY event for the batch)
```

### Node 1 — `load_hot_contacts`
- **Purpose:** Pull the current hot contact list from the scores table.
- **Tools called:** `load_hot_contacts(candidate_contact_ids | None, limit=25)`
- **Query:** `scores` JOIN `contacts` JOIN `accounts`, filtered to `segment = "hot"`, ordered by `run_date` desc, deduped by contact_id (keeps most recent score row).
- **Output:** `state.hot_contacts` (list of score+contact+account dicts)
- **Failure mode:** Supabase error → exception propagates, run fails.

### Node 2 — `propose`
- **Purpose:** Generate one `OfferProposal` per hot contact.
- **Per-contact sequence:**
  1. `_strongest_pain(contact_row)` — extracts pain signal from `explanations.why_now` (from RATE) or falls back to `accounts.pain_signal`
  2. `find_similar_wins(persona, pain)` → RAG over `playbooks` namespace (threshold 0.78) to surface similar historical proposals
  3. `synthesize_proposal(router, contact_row)` → Tier.HEAVY (Claude Opus), `max_tokens=1800`, `temperature=0.3`
- **Package→demo validation:** `_validate_package_demo_pair(package, demo)` enforces catalog-level consistency. If the LLM picks a valid package but a mismatched demo, the catalog default demo is substituted.
- **Output:** `state.proposals` (list of `OfferProposal` with `service_package`, `demo_reference`, `price_band`, `value_est_usd`, `rationale`, `playbook` [30/60/90 phases], `memo_md`, `confidence`, `similar_account_ids`)
- **Failure mode:** per-contact synthesis error → logged as `offer_synth_failed`, contact skipped, batch continues.

### Node 3 — `hitl_review`
- **Purpose:** Build the batch review card and create a `founder_review_queue` row.
- **Tools called:** `create_founder_review_task(...)` from `omerion_core/hitl/review.py`
- **Card includes:** proposal count, avg value, package distribution; per-proposal: contact ID, persona+tier, package, demo, value estimate, confidence, rationale, first 500 chars of memo
- **Output:** `state.review_id`, `state.hitl_review_id`
- **Skips node:** if `state.proposals` is empty

### Node 4 — `hitl_wait`
- **Purpose:** Suspend graph at `langgraph.types.interrupt(...)`. PostgresSaver checkpoints `OfferMatchingState`.
- **Replay guard:** checks `state.decision in ("approved", "rejected")` before calling `interrupt()` — prevents re-blocking on an already-resolved review when the graph resumes from a checkpoint.
- **Output:** `state.decision` ∈ `{"approved", "rejected"}`, optional `state.scratch["decision_notes"]`

### Node 5 — `persist`
- **Purpose:** Write approved proposals to Supabase.
- **Per-proposal sequence:**
  1. `write_opportunity(proposal)` → INSERT into `opportunities` (not upsert — no idempotency on retry)
  2. `write_memo_draft(proposal, opportunity_id)` → INSERT into `generated_drafts` with `purpose="offer_memo"`
- **Skips node:** if `state.decision != "approved"`
- **Output:** `state.opportunities_created`, `state.scratch["opportunity_ids"]` (list of UUIDs)
- **Note:** `write_opportunity` skips proposals with no `service_package` (e.g. if LLM failed to assign one).

### Node 6 — `emit`
- **Purpose:** Publish batch-level events.
- **Events emitted:**
  - `PROPOSAL_READY` (`proposal.ready`) once per approved batch — payload `{opportunity_ids, stats: {count, avg_value, packages}}`.
  - `PROPOSAL_DRAFT_READY` (`proposal.draft.ready`) — emitted only when ≥1 proposal has `confidence ≥ 0.75`; payload `{opportunity_ids, high_confidence_count, max_confidence, stats}`. The broker routes this to **build-orchestrator** (the high-confidence path that can start a build).
- **One event per batch** (not per opportunity) — downstream consumers process the full list.
- **Skips node:** if `state.decision != "approved"` or `state.opportunities_created == 0`

## Output Contract
- **Supabase `opportunities`:** INSERT per approved proposal with `contact_id`, `account_id`, `stage="engaged"`, `service_package`, `demo_reference`, `value_est_usd`, `price_band`, `metadata` (persona, rationale, playbook, confidence, similar_account_ids)
- **Supabase `generated_drafts`:** INSERT per approved proposal with `purpose="offer_memo"`, `draft_body` (the memo markdown), `draft_metadata` (package, demo, persona, value, confidence)
- **Event emitted:** `PROPOSAL_READY` once per approved batch

## Guardrails
- **ONE package per contact** — `synthesize_proposal` is prompted to pick exactly one service package. The `_validate_package_demo_pair` function enforces the catalog pairing after generation.
- **Memo must cite ICP signals.** `OFFER_SYSTEM` prompt requires grounding in the pain signal and score explanations from RATE. Generic memos that don't reference the contact's specific signals should be flagged by the founder in HITL.
- **No price ranges in memo** — founder sets actual pricing during the sales conversation; the memo carries `price_band` for internal reference only, not as a client-facing commitment.
- **Hot contacts only** — `load_hot_contacts` filters strictly on `segment = "hot"`. Never propose to warm or watchlist contacts in this pipeline.

## Stop Conditions
- **No hot contacts:** `propose` and all downstream nodes skip; run completes cleanly.
- **All proposals fail synthesis:** `state.proposals` empty; HITL card skipped; run completes.
- **Batch rejected by founder:** `persist` logs rejection and returns; nothing written to `opportunities`.

## Idempotency Rules
- `write_opportunity` **UPSERTs** on `idempotency_key` (`opportunities_idempotency_uidx`, migration 0040) with `ignore_duplicates=True`. The key is `generate_key(scope="opportunity.offer_matching", {contact_id, service_package}, window="day")` — so re-running PAIR for the same contact+package on the same day is a clean no-op (returns no row → `write_memo_draft` skipped, `opportunities_created` not double-counted). This replaced a plain INSERT that would raise a unique-violation and crash `persist_node` mid-batch on any re-run.
- `write_memo_draft` uses INSERT and only runs when `write_opportunity` returned a *new* `opportunity_id`, so it inherits the same dedupe (no opportunity row → no memo).
- `find_similar_wins` RAG call is read-only — safe to re-run.

## Fallback Protocol
- **Pinecone RAG miss in `find_similar_wins`:** `similar_account_ids` = `[]`; proposal generated without historical playbook context.
- **Pinecone error in `find_similar_wins`:** exception caught, logs `offer_rag_failed`, returns `[]`. Does not block synthesis.
- **LLM parse produces invalid package:** `_validate_package_demo_pair` returns `None`; `write_opportunity` skips the row (no package = no opportunity). Contact is NOT re-queued automatically.
- **HITL timeout:** Discord reminder fires from `omerion_core/notifications/hitl.py`. Run stays suspended; does not auto-reject.

## Model Tier Rationale
**Opus (Tier.HEAVY) for `synthesize_proposal`** — the memo must connect the contact's dossier signals (10-K, hiring patterns, LinkedIn pain signals, score explanations) to the right service package with a compelling 30/60/90 narrative. It must be persuasive enough for the founder to send it unchanged. Sonnet produces mechanically correct but generic proposals that require heavy founder editing. `temperature=0.3` keeps the output grounded and structured while allowing enough variation for persona-differentiated voice.

**No Haiku anywhere** — this agent is the revenue conversion step. Every token of quality pays for itself.

## Observability
- **Langfuse trace prefix:** `pair.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `opportunities_created` per week
  - `package_distribution` — which packages are being selected most (signals ICP quality)
  - `avg_value_est_usd` — pipeline value indicator
  - `avg_confidence` over proposals — below 0.5 means unclear pain signals
  - `hitl_approval_rate` — founder approval rate per batch; below 70% signals prompt quality issue
  - `rag_hit_rate` = proposals with `similar_account_ids` non-empty / total proposals

## Config Reference
All runtime config under `config/agents.yaml → offer_matching` and `→ offer_packages`:

| Key | Purpose |
|-----|---------|
| `rag_similarity_threshold` | Pinecone cosine threshold for `playbooks` namespace RAG (default: 0.78) |
| `offer_packages.*` | Service package definitions: name, demo_reference, price_band, timeline |
| `demo_catalog.*` | Live demo system definitions: one-liner, URL, paired package |
| `personas.*` | Persona tier lookup (shared config, used by `_persona_tier()`) |
