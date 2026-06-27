---
name: builder
tier: A
agent_number: 11
graph: agents.builder.graph:build
triggers:
  - event:build.task.created
events_consumed:
  - build.task.created
events_emitted:
  - build.task.completed
  - build.task.failed
hitl: false
hitl_on_failure: true
hitl_failure_threshold: 3
model_tier: TIERED              # Opus for high complexity, Sonnet for medium/low (driven by TaskSpec.complexity_estimate)
concurrency:
  lock: pg_advisory_lock
  key: deployment_id
---

# 0011 — BUILDER (Agent #11)

## Identity & Scope

BUILDER is the **autonomous execution arm of the Agentic Factory**. It receives a
`build.task.created` event from the ORCHESTRATOR and autonomously writes, tests,
commits, and PRs the code required by each `TaskSpec`. It does not ask for
permission to write code; it writes it, tests it, and opens the PR.

BUILDER operates strictly within the scope of each `TaskSpec`. It MUST NOT touch
files outside the scope implied by `TaskSpec.acceptance_criteria` and
`TaskSpec.rationale`.

## Trigger & Input Contract

**Event:** `build.task.created`

**Payload consumed:**
```json
{
  "blueprint_id": "<uuid>",
  "deployment_id": "<uuid>",
  "task_count": 3,
  "tasks": [
    {"slug": "add-auth", "phase": "phase_1", "title": "Add JWT auth middleware"}
  ]
}
```

Full `TaskSpec` records (including `acceptance_criteria`, `rationale`,
`files_touched_estimate`) are loaded from the `build_tasks` Supabase table
by `task_slug` + `deployment_id`.

## Reasoning Chain (5-node graph)

### Node 1 — `load_tasks`
Loads full `TaskSpec` records from `build_tasks` table for every slug in the event
payload. Validates all tasks are in `branch_open` or `issue_created` status before
proceeding.

### Node 2 — `execute_builds`
For each task (sequential), runs the inner write→test→commit→PR loop:

1. Fetch current file tree from the task's branch via GitHub Contents API.
2. Call Claude with `BUILDER_SYSTEM` + `CODE_GEN_USER` prompt, using
   **tiered model selection based on `TaskSpec.complexity_estimate`**:
   - `complexity_estimate == "high"` → Tier HEAVY (Claude Opus) —
     multi-file refactoring, complex cross-references, novel patterns
   - `complexity_estimate ∈ {"medium", "low"}` → Tier DEFAULT (Claude Sonnet) —
     standard features, config, boilerplate, test scaffolding
   Receives a JSON list of `{path, content}` file changes.
3. Clone repo to a tempdir, apply changes, run `pytest` (or the test command from
   `agents.yaml`). Capture stdout/stderr.
4. If tests PASS: commit changes to branch via GitHub Contents API, then open PR.
5. If tests FAIL and `attempt < max_retries (3)`: feed error context back to Claude
   (append error to messages) and retry from step 2.
6. If tests FAIL on attempt 3: mark task `failed`, add to `state.failed_slugs`.

### Node 3 — `hitl_escalate` (failure escalation — NOT an approval gate)
Reached only when a task is `failed` **and** the founder-retry budget remains
(`founder_retry_count < max_founder_retries`, default 1). Creates a `#founder-hitl`
card framed as **retry vs abandon**, listing each failed task + last test output.
Builder has no mandatory gate — it only opens PRs (reversible); the real deploy
gate is the orchestrator's G3 at merge.

### Node 4 — `hitl_wait` (actionable)
`interrupt()` — reads the founder's decision:
- **Approve → retry:** sets `retry_requested`, increments `founder_retry_count`,
  clears the review id, and the graph **loops back to `execute_builds`** to re-run
  the failed tasks (already-open PRs are skipped idempotently). The cap in
  `_has_failures` guarantees the cycle terminates.
- **Reject → abandon:** proceeds to `emit_summary` (emits `BUILD_TASK_FAILED`).

(Previously this interrupt threw the decision away — a blocking pause that did
nothing with the answer. Now it's wired to actually retry or abandon.)

### Node 5 — `emit_summary`
For each task: emits `build.task.completed` (if PR opened) or `build.task.failed`
(if all retries exhausted). Updates `build_tasks` row status in Supabase.

## Output Contract

Per task on success:
- `build_tasks.status` → `"pr_open"`
- PR created with acceptance criteria as `- [ ]` checklist in body
- `build.task.completed` event emitted

Per task on failure:
- `build_tasks.status` → `"failed"` with `notes` containing last pytest output
- `build.task.failed` event emitted
- HITL card created if not already escalated

## Stop Conditions

- Task's branch does not exist in GitHub → mark failed immediately, do not retry.
- `TaskSpec.acceptance_criteria` is empty → fill default `["{title} verified end-to-end."]`.
- Generated file change touches a path outside the repo root or contains a secret pattern → reject and retry with constraint appended to prompt.

## Idempotency Rules

- Idempotency key: `build.task.completed:{deployment_id}:{task_slug}` — if a PR
  already exists for this task branch, skip to `emit_summary`.
- Mutex: `pg_advisory_lock` on `deployment_id` so concurrent BUILDER triggers
  for the same deployment don't double-process.

## Fallback Protocol

1. GitHub Contents API commit fails → log and mark task `failed` (do not retry; branch state may be corrupt).
2. `pytest` unavailable in container → fall back to GitHub CI polling (`poll_ci_status` tool) for 10 minutes.
3. Claude generation returns malformed JSON → re-prompt with schema reminder; count as an attempt.

## Model Tier Rationale

**Tiered selection based on `TaskSpec.complexity_estimate`** (v2 — cost optimized):
- **Tier HEAVY (Claude Opus)** for `complexity_estimate == "high"` — multi-file
  refactoring requiring understanding of project conventions, cross-agent
  dependencies, and test infrastructure. This is the hardest reasoning task.
- **Tier DEFAULT (Claude Sonnet)** for `complexity_estimate ∈ {"medium", "low"}`
  — standard feature implementation, config changes, boilerplate, and test
  scaffolding. Sonnet handles these reliably at 1/15th the cost.

**Estimated savings:** 40–60% of BUILDER LLM costs. Empirical observation:
~65% of TaskSpecs are medium/low complexity. The `complexity_estimate` is
set by SPEC ARCHITECT (#26) during decomposition, ensuring the cost decision
is made at spec time, not build time.

Tier DEFAULT for PR body authoring (unchanged).

## Observability

- `@traced_node` on all 5 graph nodes.
- `state.record_llm()` called after every Claude call with usage dict.
- Structured log keys: `builder.task_started`, `builder.attempt`, `builder.test_result`,
  `builder.committed`, `builder.pr_opened`, `builder.task_failed`.
