---
name: qa-tester
tier: A
agent_number: 32
graph: agents.qa_tester.graph:build
triggers:
  - event:build.task.completed
events_consumed:
  - build.task.completed
events_emitted:
  - qa.tests.passed
  - qa.tests.failed
hitl: true
hitl_on_failure: true
model_tier: DEFAULT
---

# QA_TESTER — Build Quality Gate (Agent #32)

## Identity & Scope

QA_TESTER is the automated quality gate for every client build. It runs the
test suite for a completed build task, evaluates coverage, and either clears
the build for deployment or escalates to the founder with a root-cause analysis.

## Design Principle: Determinism First

The pass/fail verdict is **100% deterministic arithmetic** — tests pass/fail,
coverage meets/misses threshold. The LLM (Claude Sonnet, Tier.DEFAULT) is invoked
ONLY when tests fail, to synthesize pytest output against the spec into an
actionable root-cause narrative. The LLM never decides whether a build passes.

## Graph (5 nodes)

1. `fetch_build_context` — Load build_task from Supabase (spec, criteria, test_command)
2. `run_tests` — Execute `pytest` as subprocess, parse output deterministically
3. `analyze_failures` — LLM root-cause narrative (ONLY on failure)
4. `qa_gate` — Deterministic pass/fail threshold check + HITL on failure
5. `emit` — Persist result + emit QA_TESTS_PASSED / QA_TESTS_FAILED

## W.A.R.T.T. Contract

- **W**: Triggered by `build.task.completed`; emits `qa.tests.passed` or `qa.tests.failed`
- **A**: LLM invoked in 1 of 5 nodes (analyze_failures) — only on failure path
- **R**: Loads spec_md + acceptance_criteria from `build_tasks` table
- **T**: Event-driven only — no cron; one run per build task
- **T**: No external MCP tools — subprocess (`pytest`) + Supabase writes only
