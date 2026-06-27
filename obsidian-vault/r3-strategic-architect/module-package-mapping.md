# Module → Package → Persona Mapping — SHAPE (R3 Strategic Architect)

**Maintained by:** SHAPE (r3_strategic_architect, Agent #13)  
**Last updated:** 2026-06-03  
**Purpose:** Canonical mapping table for proposal `target_module` to service package and
target ICP persona. This is the single source of truth — the system prompt (SYNTHESIZE_SYSTEM)
and the meeting_intelligence agent must agree with this table.

---

## Canonical Mapping Table

| `target_module` | Service Package | Primary ICP Persona | Demo Reference |
|---|---|---|---|
| `daam` | revenue_acceleration_engine | revenue_leader | DAAM |
| `capa` | ops_intelligence_layer | ops_leader | CAPA |
| `remi` | research_decision_stack | professional_services_owner | REMI |
| `asap` | process_automation_suite | sme_founder | ASAP |
| `internal_os` | *(internal — not client-facing)* | *(internal)* | N/A |

---

## Module Descriptions

### daam — Revenue Acceleration Engine
**Core problem solved:** Speed-to-lead, outreach automation, and CRM pipeline acceleration.  
**When to propose:** R1 signals about CRM latency, follow-up drop-off, lead response time.
PROVE reports showing negative deltas on `speed_to_lead` or `conversion_rate`.  
**Typical Reach:** 6–10 (broad: applies to all revenue_leader accounts using any CRM)  
**Effort floor:** M (at minimum — DAAM integrations require webhook plumbing + LLM layer)

### capa — Ops Intelligence Layer
**Core problem solved:** Executive time recovery, voice-of-customer synthesis, reporting
automation for ops leaders.  
**When to propose:** R1 signals about ops reporting time, CRM data quality, executive
time sinks. PROVE reports with negative deltas on `owner_hours_saved`.  
**Typical Reach:** 4–7 (ops leaders in SME/scale-up accounts)  
**Effort floor:** M (CAPA requires data pipeline + structured extraction layer)

### remi — Research Decision Stack
**Core problem solved:** Capital allocation research automation for real estate operators
and professional services firms analyzing property markets.  
**When to propose:** R1 signals about proptech, real estate AI, CRE data needs.
PROVE reports from real estate client deployments.  
**Typical Reach:** 2–5 (niche: real estate operators only)  
**HARD CONSTRAINT:** REMI is real-estate-only. If no real estate account is in the
current ICP segment, DO NOT propose `remi`. Default to `daam` or `asap` instead.

### asap — Process Automation Suite
**Core problem solved:** Document generation, compliance workflow, multi-step approval
automation for SME founders and ops leaders.  
**When to propose:** R1 signals about document bottlenecks, compliance overhead, manual
approval chains. R2 candidates with `integration_type = "workflow"`.  
**Typical Reach:** 5–8 (cross-segment: applies broadly to SME founders with admin pain)  
**Effort floor:** M (workflow automation requires state machine + HITL gates)

### internal_os — Internal Platform Improvements
**Core problem solved:** Improvements to Omerion's own agent fleet, Supabase schema,
Pinecone indices, or LangGraph graph architecture.  
**When to propose:** R2 OSS candidates with `integration_type = "internal_tooling"` or
`integration_type = "drop-in"` that directly improve Omerion's build velocity.
R1 insights about Claude/Pinecone/Supabase version updates.  
**Typical Reach:** Uses internal headcount metric (1–3), not ICP account count.  
**Who reviews:** Founder reviews same as client-facing proposals. HITL gate applies.

---

## Forbidden Mappings (Hard Stops)

These combinations are NEVER valid. The code validates `target_module` against
`_VALID_MODULES` but does not enforce the persona/industry guard — that is a prompt-level
constraint enforced by this document and the SYNTHESIZE_SYSTEM guardrails section.

| Forbidden combination | Reason |
|---|---|
| `remi` for non-real-estate account | REMI is proptech-only; wrong package wastes proposal slot |
| `internal_os` with a client-facing `target_service_package` | internal_os is never billed to a client |
| Any module mapped to the wrong `service_package` | Violates canonical mapping above |
| Two proposals with the same `target_module` in one batch | Max 1 proposal per module per weekly run (prevents flooding one area) |

---

## Conflict Resolution

When two modules are both valid candidates for the same signal set, use this tiebreak:

1. **Prefer the module with higher PROVE evidence.** If attribution reports show negative
   KPI deltas that map to one module more directly, that module wins.

2. **Prefer the module with lower effort floor.** If signals are ambiguous between CAPA
   and ASAP for the same pain, prefer ASAP (faster to ship a doc workflow than a full
   ops intelligence layer).

3. **Do not propose the same module twice.** Merge the signals into one higher-confidence
   proposal rather than creating two competing proposals for the same module.
