# Healer — Root Cause Playbook

Last updated: 2026-06-04
Maintained by: FIX (healer, Agent #16)

Decision tree: telemetry signal → root cause → preferred intervention.
Read before formulating any patch. Escalate if the root cause is not in this table.

## High Error Rate (error_rate ≥ 30%)

| Error signal in error_log | Likely cause | Intervention |
|--------------------------|-------------|-------------|
| HTTP 429 | External API rate limit | config_patch: increase backoff, reduce concurrency |
| HTTP 500/503 from Anthropic | Provider degradation | config_patch: increase retry_attempts, add 60s base wait |
| JSONDecodeError / json.loads crash | LLM returning non-JSON | prompt_update: add stricter JSON output instruction |
| Pydantic ValidationError | State field missing or wrong type | escalate: schema mismatch, not self-healable |
| KeyError on dict access | Code assumes key that doesn't exist | escalate: code bug |
| AttributeError: state.inputs | Event_ingress mapping missing | escalate: infrastructure issue |
| Tier enum ValueError | Invalid tier string | escalate: enum drift, code fix needed |

## High Latency (p95 ≥ 30s)

| Signal | Likely cause | Intervention |
|--------|-------------|-------------|
| Slow Anthropic response in Langfuse traces | Provider latency spike | config_patch: reduce max_tokens, switch to faster tier |
| Long Supabase query durations | Missing index or table scan | escalate: DBA action needed |
| Multiple LLM calls in tight loop | Agent over-calling | prompt_update: add "stop after N tool calls" instruction |
| External API calls timing out | Downstream service slow | config_patch: reduce timeout, add fallback |

## High Cost (cost_per_run trending to ceiling)

| Signal | Likely cause | Intervention |
|--------|-------------|-------------|
| max_tokens near limit on every run | Prompt too verbose | prompt_update: trim system prompt + reduce max_tokens |
| Opus used where Haiku sufficient | Wrong tier configured | config_patch: downgrade tier for non-synthesis nodes |
| Infinite tool-call loop | Routing or termination bug | escalate: code fix needed |

## Must Escalate (do not attempt patch)

- `recent_fix_count >= 2` for this agent in past 7 days — loop guard active
- Root cause is a code bug (KeyError, AttributeError, logic error)
- Root cause is infrastructure (DB index, missing event routing, ENUM drift)
- Confidence in diagnosis < 0.50 after two retry attempts
- Target surface is outside config or prompt (e.g. graph structure, state model)
