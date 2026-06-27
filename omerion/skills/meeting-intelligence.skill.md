---
name: meeting-intelligence
tier: A
agent_number: 8
graph: agents.meeting_intelligence.graph:build
triggers:
  - webhook:fireflies.transcript_ready
events_consumed:
  - meeting.transcript_received
events_emitted:
  - blueprint.draft.created
  - blueprint.approved
  - blueprint.rejected
hitl: true
model_tier: OPUS                   # Tier.HEAVY for W5H + TTWA + proposal + backlog; Sonnet for persona + flags
rate_limits:
  - fireflies
  - anthropic
---

## Canonical Mapping

| Operator Archetype | Service Package | Demo | Scope | R&D `impact_tag` |
|---|---|---|---|---|
| `high_velocity` | `revenue_acceleration_engine` | `DAAM` | Universal | `daam` |
| `system_multiplier` | `ops_intelligence_layer` | `CAPA` | Universal | `capa` |
| `system_multiplier` | `process_automation_suite` | `ASAP` | Universal | `asap` |
| `capital_allocator` | `research_decision_stack` | `REMI` | Real Estate Only | `remi` |

**Package tiebreak for `system_multiplier`:** CRM/voice/exec-time pain → `ops_intelligence_layer` (CAPA); doc-gen/workflow/compliance pain → `process_automation_suite` (ASAP).
**REMI constraint:** `research_decision_stack` is Real Estate ONLY — recommending to a non-RE prospect must raise `persona_tier_mismatch` flag.

# CAPTURE — Meeting Intelligence & Consulting Proposal (Agent #8)

## Identity & Scope
CAPTURE converts a Fireflies discovery-call transcript into a structured Consulting
Proposal, complete with W5H profile, TTWA analysis, service package recommendation,
30/60/90 delivery plan, and an internal build backlog. It is the sole entry point
for client project work — every approved blueprint becomes the build spec for RUN
(Agent #9). CAPTURE does **not** execute builds (RUN owns that). It does **not**
generate proposals without a transcript. It will never emit `blueprint.approved`
without an explicit founder approval decision.

## Trigger & Input Contract
- **Trigger:** Fireflies webhook posts `transcript_ready` with `meeting_id`.
  Routed via `omerion_core/inbound/app.py → discord_route.py → run_executor`.
- **Input:** `state.meeting_id` (str) — the Fireflies meeting UUID.
- **No upstream events consumed.** This agent is webhook-driven, not event-driven.
- **DO NOT trigger from cron** — each call must correspond to a real discovery
  meeting. Manual trigger is allowed for re-processing with `seek run` equivalent.

## Reasoning Chain (13-node LangGraph graph)

```
fetch
  → embed_transcript       ← embeds the transcript ONCE (not per regen attempt)
  → extract_w5h
  → extract_ttwa
  → classify_persona
  → synthesize_proposal
  → build_backlog
  → raise_flags
  → persist
  → create_review
  → hitl_wait              ← interrupt(); PostgresSaver checkpoints here
  → emit_approved          ← if decision == "approved"
  → emit_rejected          ← if decision == "rejected" (after MAX_REGEN=3 attempts)
     └── loop back to synthesize_proposal with founder_feedback injected
```

### Node 1 — `fetch`
- **Purpose:** Pull full transcript text and sentences from Fireflies.
- **Tools called:** `fetch_transcript(meeting_id)` via `fireflies_client().transcript(meeting_id)`
- **Output:** `state.transcript_text` (speaker-prefixed lines), `state.transcript_sentences`, `state.summary_raw`
- **Failure mode:** Fireflies 404 → `fetch_transcript` raises; node propagates exception → run_executor marks run `failed`; no partial state is written. Webhook retry will re-enter (idempotency key on blueprints prevents double-persist).

### Node 2 — `extract_w5h`
- **Purpose:** Extract the W5H profile — the canonical input for all downstream nodes.
- **Tools called:** `extract_w5h(router, transcript_text)` → Tier.HEAVY (Claude Opus), `max_tokens=1200`
- **W5H slots** (business-operator focused, not generic SaaS):
  - `who` — decision makers, role-tagged (founder / ops leader / revenue leader / department head)
  - `what` — the specific operational problem in the prospect's own words
  - `where` — industry, org footprint, platforms where the broken workflow lives
  - `when` — urgency signals: quarter-end, budget cycle, headcount freeze, contract renewal
  - `how_much` — budget band, current spend being displaced, economic buyer
- **Output:** `state.blueprint.w5h` (W5H model)
- **Failure mode:** JSON parse fails → **raises `ValueError`** (hard fail — not silenced). An empty W5H is indistinguishable from a sparse meeting and would corrupt every downstream node. The run fails; the operator must re-trigger.

### Node 3 — `extract_ttwa`
- **Purpose:** Distill Trigger / Tension / Winning Action from the W5H.
- **Tools called:** `extract_ttwa(router, w5h, transcript)` → Tier.HEAVY, `max_tokens=500`
- **TTWA slots:**
  - `trigger` — the event that made this problem urgent now
  - `tension` — the cost of inaction in dollars or deals over 90 days
  - `winning_action` — maps to exactly one of the four service packages
- **Output:** `state.blueprint.ttwa` (TTWA model)
- **Failure mode:** JSON parse fails → **raises `ValueError`** (hard fail).

### Node 4 — `classify_persona`
- **Purpose:** Classify prospect against Omerion's 9-persona taxonomy AND derive the **Operator Archetype** (the primary Sales axis).
- **Tools called:** `classify_persona(router, w5h, transcript)` → Tier.DEFAULT (Sonnet), `max_tokens=200`
- **Archetype derivation:** after persona resolution, calls `omerion_core.personas.archetype_for(persona)` — reads `config/agents.yaml:personas[*].archetype`. This is the PRIMARY axis for package selection in Node 5.
- **Allowed personas:** `ops_leader | revenue_leader | sme_founder | agency_owner | ecommerce_operator | professional_services_owner | saas_founder | hr_talent_leader | finance_ops`
- **Output:** `state.blueprint.persona`, `state.blueprint.persona_tier` (1/2/3), `state.blueprint.archetype`
- **Failure mode:** unknown persona → coerced to `"unknown"`, archetype defaults to `"system_multiplier"`, tier defaults to 3. Does not block run.

### Node 5 — `synthesize_proposal`
- **Purpose:** Generate the `consulting_v1` Consulting Proposal JSON.
- **Tools called:** `synthesize_proposal(router, persona, persona_tier, w5h, ttwa, constraints, archetype)` → Tier.HEAVY, `max_tokens=2500`
- **PRIMARY AXIS — Operator Archetype → package** (injected as the first line of `PROPOSAL_USER`):
  - `high_velocity` → `revenue_acceleration_engine` (demo: DAAM)
  - `system_multiplier` → `ops_intelligence_layer` (demo: CAPA) or `process_automation_suite` (demo: ASAP) — tiebreak on W5H `what`
  - `capital_allocator` → `research_decision_stack` (demo: REMI, Real Estate ONLY)
- **Secondary:** persona/tier used for 30/60/90 tuning and pricing rationale, not for package selection.
- **Output:** `state.blueprint.proposal` (ConsultingProposal: exec_summary, problem_statement_w5h, operator_archetype, recommended_service_package, demo_reference, demo_plan, thirty_sixty_ninety, pricing, success_metrics, next_steps)
- **Regen loop:** on founder rejection with feedback, `founder_feedback` is injected into `constraints` and this node re-executes (max `MAX_REGEN=3` attempts total).
- **Failure mode:** JSON parse → `_parse_json` returns `{}`, ConsultingProposal built from empty dict. `raise_flags` node will catch the resulting `scope_exceeds_pricing_band` or `persona_tier_mismatch` signal.

### Node 6 — `build_backlog`
- **Purpose:** Decompose the proposal into a phased delivery backlog for internal use.
- **Tools called:** `build_backlog(router, proposal, constraints)` → Tier.HEAVY, `max_tokens=2000`
- **Output:** `state.blueprint.backlog` — list of `BacklogItem` (phase, title, rationale, effort_days, depends_on)
- **Phases:** phase_1 (0–30 days MVP), phase_2 (30–60 days integrations), phase_3 (60–90 days measurement + handoff)
- **Failure mode:** parse fails → backlog is empty list. Run continues; founder sees empty backlog in HITL card and can request regen.

### Node 7 — `raise_flags`
- **Purpose:** LLM-assisted HITL watchlist. Surfaces issues the founder must resolve before the proposal reaches the prospect.
- **Tools called:** `raise_flags(router, draft)` → Tier.DEFAULT (Sonnet), `max_tokens=300`
- **Allowed flags** (validated against `config/agents.yaml → meeting_intelligence.hitl_flag_conditions`):
  - `low_transcript_confidence` — partial or noisy transcript
  - `ambiguous_budget` — no clear budget band stated
  - `unclear_timeline` — no trigger or deadline surfaced
  - `conflicting_stakeholder_input` — decision makers disagreed on-call
  - `scope_exceeds_pricing_band` — recommended scope won't fit the price
  - `persona_tier_mismatch` — package choice fights the persona tier
- **Output:** `state.blueprint.hitl_flags` (list), `state.blueprint.confidence` (0.0–1.0)
- **Failure mode:** pure LLM call — parse fail → empty flags list, confidence = 0.5. Does not block run.

### Node 8 — `persist`
- **Purpose:** Write the draft blueprint row. (Transcript embedding moved to the
  dedicated `embed_transcript` node right after `fetch`, so the reject→regenerate
  loop — which re-runs `persist` — no longer re-embeds the same transcript 2–3×.)
- **Tools called:** `persist_blueprint(draft, meeting_id, correlation_id)` → Supabase INSERT into `blueprints`. `chunk_and_embed_transcript(...)` → Pinecone `transcripts` namespace now runs once in `embed_transcript`.
- **Output:** `state.blueprint_id` (UUID)
- **Important:** `persist_blueprint` uses INSERT (not upsert). Duplicate webhook fires for the same `meeting_id` will create a second draft row. Idempotency must be enforced upstream (Fireflies webhook dedup or inbound rate-limit).
- **Pinecone upsert:** chunk ID format `transcript:{meeting_id}:{chunk_index}` — safe to re-run.

### Node 9 — `create_review`
- **Purpose:** Build the HITL Discord card and create a `founder_review_queue` row.
- **Tools called:** `create_founder_review_task(...)` from `omerion_core/hitl/review.py`
- **Card includes:** persona + tier, confidence score, active flags, W5H.what, recommended package + price, demo plan
- **Output:** `state.review_id`, `state.hitl_review_id`
- **Event emitted:** `BLUEPRINT_DRAFT_CREATED` (before the interrupt, so the founder gets notified)

### Node 10 — `hitl_wait`
- **Purpose:** Suspend graph at `langgraph.types.interrupt(...)`. PostgresSaver checkpoints `MeetingState` here — the run sleeps cost-free waiting for the founder's Discord button click.
- **Output:** `state.decision` ∈ `{"approved", "rejected"}`, optional `state.scratch["decision_notes"]`

### Nodes 11a / 11b — `emit_approved` / `emit_rejected`
- **`emit_approved`:** Updates `blueprints.status = "approved"` (raises `RuntimeError` if no row updated — DB must be in sync). Emits `BLUEPRINT_APPROVED` → RUN (Agent #9) consumes this.
- **`emit_rejected`:** Updates `blueprints.status = "rejected"`. Emits `BLUEPRINT_REJECTED` with `regen_attempts` count and founder notes.
- **Regen path:** after rejection, if `hitl_regen_attempts < MAX_REGEN`, the conditional edge routes back to `synthesize_proposal` with the founder's feedback injected as a constraint. After 3 attempts, falls through to `emit_rejected`.

## Output Contract
- **Supabase table `blueprints`:** row with `meeting_id`, `persona`, `persona_tier`, `w5h` (JSON), `ttwa` (JSON), `proposal` (JSON, schema `consulting_v1`), `proposal_schema_version`, `constraints`, `backlog` (JSON array), `hitl_flags`, `confidence`, `status` (draft → approved/rejected), `correlation_id`
- **Pinecone namespace `transcripts`:** chunks with `transcript:{meeting_id}:{i}` IDs + metadata (`agent_type`, `account_id`, `contact_id`, `source_url`)
- **Events emitted:** `blueprint.draft.created` (on review creation), `blueprint.approved` (on founder approval), `blueprint.rejected` (on final rejection)
- **State counters:** `hitl_regen_attempts` (how many times proposal was regenerated)

## Guardrails
- **NEVER emit `blueprint.approved`** without explicit `state.decision == "approved"` — the `emit_approved` node raises `RuntimeError` if the DB update fails.
- **NEVER invent facts.** W5H extraction rule: if a slot is not addressed on the call, leave it empty. Quoted prospect language only — no marketing hype.
- **NEVER guess the service package** in W5H or TTWA. Package selection happens in `synthesize_proposal` only.
- **No partial proposals.** If W5H or TTWA extraction raises a `ValueError`, the run fails hard rather than silently producing a downstream blueprint with corrupt inputs.

## Stop Conditions
- **Fireflies fetch fails:** run fails, no blueprint written. Webhook retry re-processes.
- **W5H or TTWA parse fails:** run fails with `ValueError`. No HITL card created.
- **Zero drafts after regen exhaustion:** `emit_rejected` fires, blueprint status set to `"rejected"`.
- **DB update fails on approval:** `RuntimeError` raised — prevents stale `"draft"` status from emitting a false `blueprint.approved` downstream.

## Idempotency Rules
- Transcript embed in Pinecone is idempotent (`transcript:{meeting_id}:{i}` upsert).
- Blueprint INSERT is **not** idempotent — duplicate webhook fires create duplicate draft rows. Route dedup must happen at the inbound webhook level.
- Once `meeting_id` has a `blueprints.status = "approved"` row, re-triggering for the same meeting creates a new draft row (not a re-approval of the existing one).

## Fallback Protocol
- **Fireflies 404:** run fails → Fireflies will retry webhook; no action needed.
- **Fireflies auth error (401):** run fails → alert via Langfuse; rotate `FIREFLIES_API_KEY`.
- **LLM parse fail on W5H or TTWA:** hard fail — do not silently continue.
- **LLM parse fail on proposal/backlog/flags:** soft fail — empty/partial structs, run continues; founder sees incomplete data in HITL card and can request regen.
- **Supabase write fails at persist:** run fails → no HITL card created; re-trigger after confirming DB.
- **HITL timeout (60 min from `global.hitl_escalation_minutes`):** Discord reminder fires from `omerion_core/notifications/hitl.py`. Run stays suspended — does not auto-reject.

## Model Tier Rationale
| Node | Tier | Why |
|------|------|-----|
| `extract_w5h` | HEAVY (Opus) | Long transcripts (10k+ tokens), dense information extraction; errors cascade to every downstream node |
| `extract_ttwa` | HEAVY (Opus) | Requires connecting multiple transcript signals to diagnose the root urgency driver |
| `synthesize_proposal` | HEAVY (Opus) | Creative + structured simultaneously; must be persuasive enough for founder to send unchanged |
| `build_backlog` | HEAVY (Opus) | Decomposition requires understanding delivery dependencies and effort estimation |
| `classify_persona` | DEFAULT (Sonnet) | Classification against a known 9-item taxonomy; lower stakes than extraction |
| `raise_flags` | DEFAULT (Sonnet) | Anomaly detection against a short allowed-flags list; Sonnet sufficient |

**Escalation rule:** If transcripts routinely exceed 24,000 characters (CAPTURE truncates W5H input at `transcript[:24000]` and TTWA at `transcript[:8000]`), switch to extended context or chunked extraction to prevent signal loss.

## Observability
- **Langfuse trace prefix:** `capture.*` (every node wrapped with `@traced_node`)
- **Key metrics to watch:**
  - `transcripts_processed` per day
  - `w5h_parse_failure_rate` — any non-zero value is a prompt regression
  - `avg_confidence` over `blueprints.confidence` — below 0.6 means noisy transcripts
  - `hitl_flag_rate` — ratio of blueprints with ≥1 flag; high rate signals poor call quality
  - `regen_rate` — `hitl_regen_attempts > 0` / total runs; high rate signals proposal quality issue
  - `blueprints_approved_rate` — approved / total; tracks founder satisfaction with output
  - `avg_blueprints_per_week` — leading revenue indicator

## Config Reference
All runtime config under `config/agents.yaml → meeting_intelligence`:

| Key | Purpose |
|-----|---------|
| `constraint_fields` | List of constraint keys injected into proposal + backlog prompts |
| `proposal_schema_version` | Persisted to `blueprints.proposal_schema_version` (currently `consulting_v1`) |
| `hitl_flag_conditions` | Allowed flag strings validated by `raise_flags` |

## Golden Output — Consulting Proposal

**Scenario:** Discovery call with a SaaS revenue leader (archetype `high_velocity`), 25 employees, Salesforce CRM, losing ~40% of inbound to slow follow-up. W5H `how_much` surfaces `$50k quarterly budget` → anchors `pricing.price_usd` within the band. W5H `when` surfaces `Q4 board review` → anchors `thirty_sixty_ninety`. TTWA `tension` (`$500k ARR/quarter lost`) justifies `impact = high`. Canonical chain: archetype `high_velocity` → `revenue_acceleration_engine` → demo `DAAM`.

```json
{
  "exec_summary": "New CRO at a 25-person SaaS company is losing ~40% of inbound leads to slow, fragmented follow-up — roughly $500k ARR/quarter at current conversion. Mandate is to 2x pipeline by the Q4 board review. We recommend the revenue_acceleration_engine to collapse speed-to-lead from hours to under a minute and unify follow-up across channels. We will prove the pattern live on DAAM during the next call.",
  "problem_statement_w5h": "WHO: new CRO (economic buyer), Head of Ops (champion), 6 BDRs. WHAT: inbound leads sit hours before first touch; ~40% are never qualified or routed. WHERE: B2B SaaS, ~25 employees, Salesforce CRM, follow-up split across email + Slack. WHEN: CRO hired Q2, committed to 2x pipeline conversion by the Q4 board review. HOW_MUCH: $3k/mo on Outreach today; CRO holds a $50k quarterly tooling budget.",
  "operator_archetype": "high_velocity",
  "persona": "revenue_leader",
  "persona_tier": 1,
  "recommended_service_package": "revenue_acceleration_engine",
  "demo_reference": "DAAM",
  "demo_plan": "Live on DAAM: feed 10 real inbound leads from your Salesforce. Show (1) AI qualification on fit + intent (~2 min/lead), (2) tiered scoring written back to Salesforce fields, (3) sub-60s routing to the on-call BDR with a Slack notification, (4) a drafted first-touch message per lead. End state: 10 leads qualified, scored, routed, and queued in under 5 minutes.",
  "thirty_sixty_ninety": {
    "30": "Connect DAAM to one inbound source (Salesforce import or webhook). Classify and score 100% of inbound 24/7, writing an AI quality score to Salesforce custom fields. Success: every inbound lead scored within 30 days.",
    "60": "Routing + speed-to-lead: qualified leads trigger a sub-60s Slack notification to the on-call BDR. Instrument first-touch latency. Success: average first-touch drops from ~4h to <60s.",
    "90": "Closed-loop attribution: link qualified leads to won deals; report conversion lift vs. baseline. Draft a co-marketing case study. Success: +15-25% follow-up conversion, client self-sufficient on the flow."
  },
  "pricing": {
    "price_usd": 12000,
    "band": [5000, 15000],
    "rationale": "Mid-band: 30-day MVP (qualification + scoring) with one Salesforce integration point and single-source ingestion. Multi-source ingestion (forms, API, LinkedIn, email) moves scope toward the $15k ceiling."
  },
  "success_metrics": [
    "% inbound leads scored (target 100% within 30 days)",
    "avg speed-to-lead (target <60s; baseline ~4h)",
    "qualification accuracy vs. BDR manual review (target >85% agreement)",
    "follow-up conversion lift at 90 days (target +15-25% vs. baseline)"
  ],
  "next_steps": [
    "Book the 30-minute DAAM walkthrough on your live leads",
    "Confirm the Salesforce integration point (custom-field export or webhook)",
    "Sign SOW; Omerion provisions the environment and connects Salesforce",
    "Go live: DAAM qualifies, scores, and routes inbound 24/7"
  ]
}
```
