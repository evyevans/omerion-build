# Proposal Templates & Golden Examples — SHAPE (R3 Strategic Architect)

**Maintained by:** SHAPE (r3_strategic_architect, Agent #13)  
**Last updated:** 2026-06-03  
**Purpose:** Golden examples of well-formed proposals with worked RICE calculations.
These are the reference standard for founder review and for SHAPE's own synthesis.
Study these before generating output — match this structure and specificity level.

---

## How to Read These Examples

Each example includes:
1. The RICE calculation with all four variables made explicit
2. The `priority_score` value (= RICE result, not the simplified Impact×Effort weight)
3. A fully-specified JSON proposal block in the output schema
4. A founder decision note explaining why it was approved or rejected

The JSON shape matches the `DesignProposal` state model exactly.

---

## Example 1 — APPROVED (RICE = 20.0)

**Proposal title:** Sub-60s speed-to-lead module for DAAM  
**Module:** daam → revenue_acceleration_engine

### RICE Calculation
```
Reach      = 8   (all revenue_leader accounts using any CRM — broad horizontal)
Impact     = 5   (>30% speed-to-lead reduction → KPI threshold for "massive")
Confidence = 1.0 (3 R1 insights + 1 PROVE report with negative speed_to_lead delta)
Effort     = M = 2

RICE = (8 × 5 × 1.0) / 2 = 20.0 → impact = "high"
priority_score = 20.0
```

### JSON Output
```json
{
  "title": "Sub-60s speed-to-lead module for DAAM",
  "problem_statement": "Revenue leaders report CRM-to-outreach lag of 4–8 hours after a new lead enters HubSpot or Salesforce. Three R1 signals in the last 14 days confirmed sub-60s response as the market benchmark; PROVE report for client A shows -18% conversion rate YoY.",
  "hypothesis": "A LangGraph webhook listener + Claude-powered DM drafter, triggering within 60s of CRM lead creation, will recover the conversion delta and differentiate the DAAM package.",
  "design_doc_md": "## Sub-60s Speed-to-Lead Module\n\n**Architecture:** Supabase Realtime trigger on `contacts.created` → LangGraph `daam_speed_node` → ClaudeRouter(Tier.DEFAULT) draft → founder HITL review (G1 gate) → LinkedIn DM or email send.\n\n**KPI target:** Reduce lead-to-first-touch from 4h average to <60s for all leads scored ≥0.7 by icp_scoring.\n\n**Integration points:** HubSpot/Salesforce webhook (existing `contacts` table), LinkedIn MCP (`linkedin_queue_dm`), Gmail MCP (`gmail_send_email`).\n\n**Risk:** Browser-use/Playwright LinkedIn session stability. Mitigation: fall back to email if DM queue depth >50.",
  "target_module": "daam",
  "impact": "high",
  "effort": "M",
  "priority_score": 20.0,
  "supporting_insight_ids": ["<uuid-r1-1>", "<uuid-r1-2>", "<uuid-r1-3>"],
  "supporting_oss_ids": [],
  "supporting_report_ids": ["<uuid-prove-1>"],
  "blueprint_handoff": {
    "phase_1": "Days 1–7: Implement Supabase Realtime listener + LangGraph daam_speed_node skeleton. Baseline current lead response time from `contacts` table. HITL gate for first 10 messages.",
    "phase_2": "Days 8–30: Expand to full CRM (HubSpot + Salesforce) via webhook ingress. Remove HITL gate after 10 approved sends. Auto-routing by lead score threshold.",
    "phase_3": "Days 31–60: Attribution report comparing speed-to-lead before vs. after. Case study draft. Verify client self-sufficiency on CRM webhook config."
  }
}
```

**Founder decision:** ✅ APPROVED — Problem is specific, RICE is credible (3 signals + PROVE), blueprint names exact tools and phases. "Phase 3 attribution gate is exactly what I need."

---

## Example 2 — APPROVED (RICE = 9.6)

**Proposal title:** Exec time recovery layer for CAPA  
**Module:** capa → ops_intelligence_layer

### RICE Calculation
```
Reach      = 4   (ops_leader accounts in scale-up segment — medium vertical)
Impact     = 3   (10–30% exec time reduction → "moderate" threshold)
Confidence = 0.8 (2 corroborating signals: 1 R1 + 1 PROVE)
Effort     = M = 2

RICE = (4 × 3 × 0.8) / 2 = 4.8 → impact = "medium"
priority_score = 4.8
```

Wait — 4.8 < 5, so impact must be downgraded to "low" by the RICE rule, OR this
proposal should not be sent with impact="medium". Correct JSON uses impact="low".

### Corrected JSON (shows the downgrade rule in action)
```json
{
  "title": "Exec time recovery layer for CAPA — structured CRM debrief",
  "problem_statement": "Ops leaders report 6–10 hours/week lost to manual CRM data entry after client calls. One R1 insight tagged 'capa' confirms market demand; PROVE report for client B shows -8% owner_hours_saved YoY.",
  "hypothesis": "A CAPA post-call debrief node — auto-extracting action items and CRM fields from meeting transcripts via meeting_intelligence output — will recover 5–8 hours/week per ops_leader client.",
  "design_doc_md": "## Exec Time Recovery — CAPA Debrief Node\n\n**Architecture:** meeting_intelligence BLUEPRINT_APPROVED event → CAPA debrief_node → ClaudeRouter(Tier.DEFAULT) extraction → Supabase `contacts` + CRM webhook update.\n\n**KPI target:** Reduce post-call CRM update time from average 45 min to <5 min per meeting.\n\n**Integration points:** fireflies_client (transcript), Supabase `blueprints` table, CRM webhook (HubSpot).",
  "target_module": "capa",
  "impact": "low",
  "effort": "M",
  "priority_score": 4.8,
  "supporting_insight_ids": ["<uuid-r1-4>"],
  "supporting_oss_ids": [],
  "supporting_report_ids": ["<uuid-prove-2>"],
  "blueprint_handoff": {
    "phase_1": "Days 1–14: Build debrief_node stub. Parse meeting_intelligence TTWA output. Write extracted fields to `contacts.scratch`.",
    "phase_2": "Days 15–45: CRM webhook push (HubSpot first). HITL review for first 5 clients. Auto-approve if confidence ≥0.85.",
    "phase_3": "Days 46–90: Measure hours-saved via `agent_telemetry`. Attribution report targeting owner_hours_saved KPI."
  }
}
```

**Founder decision:** ✅ APPROVED — "impact=low is correct per the formula, that's fine. The build is simple and the time-save is real. Phase 1 in 2 weeks is believable."

---

## Example 3 — REJECTED (RICE = 1.875)

**Proposal title:** AI-powered market research digest for REMI  
**Module:** remi → research_decision_stack

### RICE Calculation
```
Reach      = 2   (real estate operators only — narrow vertical, 1–2 active clients)
Impact     = 1   (<5% KPI movement — proptech data digests are directionally useful
                  but rarely move a specific measurable KPI by >5%)
Confidence = 0.5 (1 signal — one R1 insight about proptech AI tools)
Effort     = L = 4

RICE = (2 × 1 × 0.5) / 4 = 0.25 → impact = "low"
priority_score = 0.25
```

**This proposal should never have been generated** — RICE = 0.25, single-signal, narrow
vertical with no PROVE data. Included here as a negative example.

### Why it was rejected
```
Founder decision: ❌ REJECTED — "No proptech clients onboarded yet. No PROVE data.
Reach=2 is generous. This is a hypothesis with no supporting evidence from our actual
deployments. Resubmit when we have one proptech client live and 3+ PROVE reports."
```

### What would make this valid next cycle
- At least 1 proptech client onboarded (adds PROVE signal source)
- 2+ R1 insights corroborating the specific CRE research workflow pain
- Raise Reach to 4+ once proptech vertical has 4+ ICP accounts

---

## Common Failure Patterns (Do Not Repeat)

| Failure | Example | Correct approach |
|---|---|---|
| Generic blueprint phases | "Phase 1: Planning and discovery" | Name the exact tools and tables being built |
| RICE mismatch | impact="high" but RICE=3.5 | Enforce the RICE→impact threshold rule before outputting |
| Fabricated supporting IDs | `"supporting_insight_ids": ["abc123"]` | Only cite UUIDs that appear in the loaded signal block |
| Wrong persona for module | `remi` for a SaaS founder | Check module-package-mapping.md before assigning target_module |
| Confidence overstated | C=1.0 with only 1 signal | Use the Confidence Decision Table in signal-synthesis-guide.md |
| Proposal for solved problem | Proposing speed-to-lead fix when PROVE shows it already improved | Check PROVE deltas before proposing; PROVE overrides R1 topic signals |
