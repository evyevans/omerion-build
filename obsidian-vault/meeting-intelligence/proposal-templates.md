# Proposal Templates

## Proposal Schema

**proposal_schema_version:** `consulting_v1`

### Required Sections (all must be present — no section may be null or empty)

1. **exec_summary** — ≤3 sentences. Lead with the pain (not the solution). No jargon. No filler phrases ("leverage synergies", "best-in-class", "unlock potential").

2. **problem_statement_w5h** — Restate the W5H as a narrative paragraph (3–5 sentences). Every claim must be grounded in a specific transcript signal. Do not introduce new claims not supported by W5H.

3. **operator_archetype** — Exactly one of: `high_velocity`, `system_multiplier`, `capital_allocator`. Include 1 sentence rationale citing the W5H evidence.

4. **recommended_service_package** — Exactly one of the 4 canonical packages (see Package Assignment Rules below).

5. **demo_reference** — Exactly one of: `DAAM`, `CAPA`, `ASAP`, `REMI`. Must match the package per the mapping table below. Mismatches are a validation error.

6. **demo_plan** — 3 numbered steps. Each step is 1–2 sentences. Steps must be tailored to this prospect's specific W5H — no generic demo scripts.

7. **thirty_sixty_ninety** — Dict with exactly 3 keys: `day_30`, `day_60`, `day_90`. Each value is 1 sentence milestone. Milestones must be measurable ("X delivered", not "progress made on X").

8. **pricing** — Object: `price_usd` (float), `band` ([int, int] min/max range), `rationale` (str ≤200 chars). Floor: $4,800/mo. Rationale must reference the prospect's stated how_much or persona tier.

9. **success_metrics** — List of 3–5 strings. Each metric must be KPI-specific and measurable within 90 days. No vanity metrics ("improved efficiency").

10. **next_steps** — List of 2–3 strings. First action must be schedulable within 48 hours ("Book 30-min technical scoping call"). Last action must be the prospect's commitment ("Send signed proposal by [date]").

### Package → Demo Mapping (enforced at validation)

| service_package | demo_reference | default_archetype |
|---|---|---|
| `revenue_acceleration_engine` | `REMI` | `high_velocity` |
| `ops_intelligence_layer` | `CAPA` | `system_multiplier` |
| `research_decision_stack` | `DAAM` | `capital_allocator` |
| `process_automation_suite` | `ASAP` | `system_multiplier` or `high_velocity` |

### Package Assignment Rules

- **research_decision_stack**: Only for clients whose primary pain is information latency, market intelligence gaps, or strategic decision bottlenecks. Default for Real Estate clients.
- **process_automation_suite**: Default for `ops_leader` and `agency_owner` with high billable-hour waste signal in W5H.
- **revenue_acceleration_engine**: Default for `revenue_leader` and `saas_founder` with pipeline velocity or conversion rate pain.
- **ops_intelligence_layer**: Default for `hr_talent_leader`, `finance_ops`, `professional_services_owner`.
- **Pricing floor:** Never quote below $4,800/mo for any package regardless of persona tier.

### Archetype → Package Priority

| archetype | first_choice | fallback |
|---|---|---|
| `high_velocity` | `revenue_acceleration_engine` | `process_automation_suite` |
| `system_multiplier` | `ops_intelligence_layer` | `process_automation_suite` |
| `capital_allocator` | `research_decision_stack` | `ops_intelligence_layer` |
