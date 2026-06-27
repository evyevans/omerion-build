# LinkedIn Outreach — Sequence Templates

Last updated: 2026-06-04
Maintained by: REACH (linkedin_outreach, Agent #4)

Two tracks. Cold starts at Step 0. Warm (prior engagement exists) starts at Step 2.
`plan_steps()` builds the sequence; `draft_message()` fills each step.

## Cold Track

| Step | Type | Day offset | Min cooldown | template_key pattern | Required context |
|------|------|-----------|-------------|---------------------|-----------------|
| 0 | connection_request | 0 | — (send immediately) | `{persona}_connect_v1` | full_name, title, company, one_specific_signal |
| 1 | intro_dm | +3d | 3d after connect accepted | `{persona}_intro_v1` | pain_signal, company_context |
| 2 | value_dm | +7d | 7d after intro sent | `{persona}_value_v1` | specific_insight, relevant_result |
| 3 | ask_dm | +7d | 7d after value sent | `{persona}_ask_v1` | clear_cta (discovery call or demo) |

## Warm Track (prior engagement — skip Steps 0–1)

Starts at Step 2 (value_dm). Treat prior contact as Step 1 completion.
Warm contacts: stage ∈ {consideration, evaluation} OR last_reply_at within 30 days.

## Daily Platform Caps (hard limits enforced in tools.py)

- Connection requests: 25/day
- Direct messages: 40/day
- If cap reached: `queue_for_sender` with `scheduled_for = next_business_day`

## template_key Format

`{persona_token}_{step_type}_v{version}`
Examples: `ops_leader_connect_v1`, `sme_founder_value_v1`, `revenue_leader_ask_v1`

Current active version: `v1` for all personas and step types.

## Context Variable Rules

- `one_specific_signal`: MUST be specific to this contact — their company, recent post, or dossier pain. Never generic industry commentary.
- `pain_signal`: Pulled from research_dossier.pain_signals or contact_activity_log email excerpt.
- `clear_cta`: One explicit ask only. "15-minute call this week?" or "Worth a quick look at a demo?"
- `prior_interaction`: For warm tracks — reference what was said previously, not just that you spoke.
