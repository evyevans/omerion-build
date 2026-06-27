---
name: build-orchestrator
tier: A
agent_number: 9
graph: agents.build_orchestrator.graph:build
triggers:
  - event:build.task.created
  - event:rd_proposal.approved
events_consumed:
  - build.task.created
  - rd_proposal.approved
events_emitted:
  - deployment.live
  - deployment.failed
hitl: true                         # HITL required — founder approves every deployment (G3)
model_tier: DEFAULT                # Sonnet for client docs; deterministic for orchestration
rate_limits:
  - github
  - anthropic
---

# RUN — Build Orchestrator (Agent #9) — SIMPLIFIED

## Identity & Scope

RUN owns the **production floor coordination** from task creation to live
deployment. It drives GitHub issue/branch/PR/CI/merge cycles, gates every
deployment behind a founder HITL review (G3), and produces client Google Doc
deliverables in post-deploy mode.

**What changed (v2 simplification):**
- **REMOVED:** Blueprint decomposition — now handled by SPEC ARCHITECT (#26).
  RUN no longer calls Claude Opus for decomposition. It receives pre-built
  `TaskSpec[]` from the `build.task.created` event.
- **REMOVED:** `build.task.created` event emission — SPEC ARCHITECT emits this.
- **CHANGED:** Trigger from `blueprint.approved` → `build.task.created`.
  RUN no longer needs to know about blueprints — it only knows about tasks.
- **CHANGED:** Model tier from OPUS → DEFAULT (Sonnet). The only LLM usage
  remaining is client doc authorship, which Sonnet handles well.
- **KEPT:** Full GitHub lifecycle, G3 HITL gate, Google Docs generation,
  deployment event emission.

RUN does **not** decompose blueprints (SPEC ARCHITECT), does **not** generate
code (BUILDER), and does **not** approve its own deployments.

## Trigger & Input Contract

- **Events consumed:**
  - `build.task.created` — from SPEC ARCHITECT (#26): contains `TaskSpec[]`
    with slugs, phases, acceptance criteria, and complexity estimates.
  - `rd_proposal.approved` — from SHAPE (R3): internal OS improvement
    proposals (unchanged from v1).
- **Mode determination:**
  - `build.task.created` with `client_id` → `mode = "client"`
  - `rd_proposal.approved` → `mode = "internal"`
- **Required state fields:** `deployment_id`, `mode`, `client_slug`,
  `repo_full_name`, `task_specs[]`.

## Reasoning Chain (8-node LangGraph graph)

> **Compared to v1 (10 nodes):** Removed `load_blueprint` and `decompose`.
> These are now SPEC ARCHITECT's responsibility.

### Node 1 — `load_tasks`
Load `TaskSpec[]` from the `build_tasks` table by `deployment_id`.
Verify all tasks are in `pending` status. If `rd_proposal.approved`,
load the proposal and create TaskSpecs inline (unchanged from v1 —
R&D proposals are simple enough to decompose in-line).

### Node 2 — `persist_deployment` (formerly `create_deployment_row`)
Insert/verify the `deployments` row with `status = "pending"`.
For `build.task.created` events, the deployment row already exists
(SPEC ARCHITECT creates it). Verify and update status to `"building"`.

### Node 3 — `build_tasks` (per-task fan-out; STOPS at ci_pass)
For each task, execute sequentially:
1. `create_issue` → GitHub issue with structured body
2. `create_branch` → branch from main per naming convention
3. `inject_to_cursor` → inject task + branch into Cursor/Antigravity
4. `poll_pr` → poll for a PR on this branch; timeout → `failed`
5. **CI gate:** `ci_status != "success"` → `ci_fail`; success → `ci_pass`
6. **Does NOT merge.** Deferred to `merge_tasks` after G3.

### Node 4 — `hitl_gate` (G3 — deploy/infra)
One HITL card lists the PRs ready to merge & deploy. Single `interrupt()`.
**Fail-closed** — no approval → nothing merges.
Skipped if no task reached `ci_pass`.

### Node 5 — `merge_tasks` (post-approval)
For each `ci_pass` task, `merge_pr` squash-merges to `main`.
`merge_pr` refuses any PR lacking a VALIDATOR approval.

### Node 6 — `finalize_deployment`
- Rejected / nothing merged: `deployments.status = "failed"`.
- Approved + all merged: `status = "live"`.

### Node 7 — `deliver_client_docs` (client mode only)
Skip in `mode = "internal"`. In client mode, generates Google Docs using
Claude Sonnet (proposals, SOWs, blueprints, handoffs) in the per-client
Drive folder.

### Node 8 — `emit_deployment`
Emit `deployment.live` or `deployment.failed`.

## Output Contract
- **Supabase:** `deployments` row + `build_tasks` rows with status transitions.
- **GitHub:** one issue + one branch + one PR per task.
- **Google Drive (client mode):** one Google Doc per doc_type.
- **Events:** `deployment.live` or `deployment.failed`.

## Stop Conditions
- All tasks `ci_fail`: present to founder for decision.
- Founder rejects deployment: `status = "failed"`, emit `deployment.failed`.
- Zero tasks in event payload: halt, log `run_zero_tasks`.

## Idempotency Rules
- `deployment_id` is the idempotency anchor.
- `build_tasks` upsert on `(deployment_id, slug)`.
- GitHub issue/branch creation wrapped in existence checks.
- Google Docs: folder is get-or-create.

## Fallback Protocol
- GitHub rate limit: backoff `[4, 15, 60]`, then mark task `failed`.
- Cursor injection fails: fall back to DeepSeek elaboration.
- Google Drive API fails: log error, mark deliverable `failed`. Docs are
  post-deploy artifacts, not deployment gates.
- Sonnet unavailable (client docs): retry with backoff. After 2 failures,
  HITL: "Client docs generation failed — manual authorship required."

## Model Tier Rationale

**Claude Sonnet (Tier.DEFAULT)** — the only LLM usage remaining is client
doc authorship (proposals, SOWs, handoffs). Sonnet produces commercially
acceptable documents for $5K–$60K engagements. Opus was previously required
for decomposition — that cognitive load is now in SPEC ARCHITECT.

**Net cost reduction:** ~60% per RUN invocation (Opus decomposition was the
most expensive step).

## Observability
- **Langfuse trace prefix:** `run.*`
- **Key metrics:**
  - `deployments_live` per week
  - `deployments_failed`
  - `hitl_approval_rate`
  - `avg_tasks_per_deployment`
  - `client_docs_generated` (client mode)
  - `ci_poll_cycles` per deployment

## Config Reference

| Key | Purpose |
|-----|---------|
| `mode` | `internal` or `client` |
| `branch_convention` | Branch naming pattern |
| `ci_poll_interval_seconds` | Seconds between CI checks (default: 30) |
| `google_drive_deliverables_root` | Drive folder ID for client docs |
