---
name: trainer
tier: B
agent_number: 16
runtime: langgraph
spec: agents.trainer.graph:build
triggers:
  - cron
events_consumed: []
events_emitted:
  - prompt.update.applied
hitl: true
model_tier: HEAVY
schedule: "0 3 * * 1"
---

# TRAINER — Chief Intelligence Officer (Agent #16, Wave 5)

## Identity & Scope

TRAINER is the agency's self-improvement loop. Once a week it reads the
last 7 days of agent telemetry, identifies which prompts caused
underperformance (failures, low confidence, cost variance, founder
overrules), and proposes rewritten prompt text grounded in the specific
failure evidence.

TRAINER **never** alters input/output schemas and **never** modifies tool
code or graph wiring. It **never** applies a prompt change without an
explicit founder approval — but on approval it *does* apply the change
itself (closing the RSI loop), rather than leaving the founder to hand-edit
`prompts.py`. The flow: proposal → HITL interrupt → founder approve →
`apply_prompt_update()` rewrites the target `prompts.py` string constant
(AST-targeted, SHA-staleness-guarded, compile-checked, backed up, logged to
`audit_log`) → `prompt.update.applied` event → AUDITOR verifies the
self-modification (Rule 3 HITL_BYPASS: the approval must exist).
Only **simple string-literal** prompts are auto-applied; f-strings or
concatenated prompts are skipped with `non-literal prompt … manual apply
required` and still require a hand-edit.

**Scope (Wave 5):** the 6 wrapper-migrated agents only —
`linkedin_outreach`, `crm_nurture`, `offer_matching`,
`meeting_intelligence`, `lead_scraper_enricher`, `outcome_attribution`.
Expanding scope = adding agent_name to `TRAINER_TARGET_AGENTS` in
`tools.py` once the agent is wrapper-migrated.

## Trigger & Input Contract

- **Trigger:** APScheduler cron `0 3 * * 1` (Mondays 03:00
  America/Toronto). Auto-discovered from this skill.md frontmatter at
  app boot by `omerion_core/runtime/scheduler.py`.
- **Input:** `TrainerInput { window_days: int = 7 }`. The wrapper
  generates `run_id`, `correlation_id`, `session_id` automatically.
- **Cohort:** empty — TRAINER does not operate on a contact cohort.
  The wrapper's opt-out filter is a no-op for this agent.

## Reasoning Chain (4 nodes)

1. **`fetch_outcomes`** — Aggregate per-agent KPIs from
   `agent_performance_metrics` + `agent_telemetry` over the rolling
   7-day window. Compute `failure_rate`, `rejection_ratio`,
   `cost_variance_ratio`. If zero target agents have telemetry in the
   window → set `no_signal=True` and short-circuit to END.

2. **`identify_underperforming_prompts`** — Deterministic threshold
   check (no LLM). Flag any agent that breached at least one of:
     - `failure_rate > 10%`
     - `cost_variance_ratio > 3×` (p95 cost / median cost)
     - `rejection_ratio > 30%` (founder overruling HITL)
     - `regression_flags >= 1` (R4 alert already fired)
   For each flagged agent, AST-parse its `prompts.py` and snapshot every
   uppercase string constant. **NEVER imports/execs the target module.**

3. **`generate_prompt_improvements`** — One Claude (Tier.HEAVY) call per
   (underperformer × prompt constant), capped at 2 prompts per agent
   per run (system prompts get priority — higher leverage per change).
   Each LLM response passes through the deterministic guardrail
   (`tools.validate_proposal_text`) BEFORE landing in state.proposals:
     - No code fences (```)
     - No `class X(BaseModel)` definitions
     - Identical format-string placeholder set vs. current_text
   Failed proposals are silently dropped (logged); the founder never
   sees a guardrail-rejected proposal.

4. **`propose_update`** — For each valid proposal:
     - Upsert into `prompt_improvements` with idempotency_key encoding
       (agent, constant, iso_week). DB UNIQUE makes a TRAINER restart
       inside the same week a silent no-op.
     - Create a `founder_review_queue` row.
   Then `interrupt()` — graph pauses until ALL pending reviews for the
   session_id are decided. On resume, update each
   `prompt_improvements.status` to 'approved' or 'rejected'.

## Output Contract

```
TrainerOutput {
  confidence: float,                    # average across emitted proposals
  proposals_count: int,                 # raw count from LLM
  proposals_persisted: int,             # passed guardrail + DB insert
  proposals_approved: int,              # founder said yes
  proposals_rejected: int,              # founder said no (or dedup-skipped)
  underperformers_count: int,
  no_signal: bool                       # true if Node 1 or 2 short-circuited
}
```

## Stop Conditions

- No telemetry rows in the 7-day window → Node 1 sets `no_signal=True`
  and the graph routes directly to END.
- All flagged agents have unparseable `prompts.py` files →
  Node 2's filtered list is empty → END.
- Every LLM proposal fails the deterministic guardrail → Node 3 produces
  zero valid proposals → END (no founder noise).

## Idempotency Rules

- Run-level: `agent_wrapper.run()` minute-window idempotency dedupes
  rapid-fire triggers.
- Proposal-level: `prompt_improvements.idempotency_key = sha256(scope +
  {agent, constant, iso_week})`. UNIQUE constraint catches duplicates
  at the DB layer.
- Iso-week scoped: `2026-W21` only. Next week's run produces a fresh
  set of keys.

## Fallback Protocol

- Supabase outage during Node 1 → log + return empty
  `performance_summaries` → Node 2 short-circuits → END. TRAINER does
  not raise; the next weekly tick retries.
- LLM outage during Node 3 → individual `generate_improvement` calls
  return None; remaining proposals proceed. If all fail, Node 3 sets
  `no_signal=True` and ends cleanly.
- HITL creation failure on a single proposal → that proposal is
  skipped; others still get their review cards. Logged at WARN.

## Observability

- Every node decorated `@traced_node(...)` — Langfuse trace per node.
- `agent_messages` rows on every event handoff (none expected for
  TRAINER but the broker is wired for it).
- `prompt_improvements` is the durable proposal history. Dashboard
  query: `SELECT target_agent_name, prompt_constant_name, status,
  decided_at FROM prompt_improvements WHERE iso_week = $week`.

## Guardrails Summary (TWAT spec §A.2)

1. **NEVER alters input/output schema** — enforced by
   `tools.validate_proposal_text` placeholder-set diff. An LLM that
   renames `{persona}` → `{persona_name}` is auto-rejected.
2. **MUST provide "Why this improves performance" rationale** —
   enforced by Pydantic `min_length=50` on `PromptProposal.rationale`
   AND a DB `CHECK (length(rationale) >= 50)` constraint. Two layers.
