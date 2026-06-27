---
name: proposal-drafting
tier: B
agent_number: null
description: AI automation consulting proposal generation (W5H + service package + 30/60/90 plan)
triggers:
  - event:opportunity.qualified
  - manual
hitl: true
---

# AI Automation Consulting Proposal Framework

End-to-end proposal authorship for qualified AI automation consulting opportunities. Used by CAPTURE to generate client-facing deliverables that include executive summary, W5H recap, service package recommendation, demo plan, pricing, success metrics, and next steps.

## Proposal Structure

### 1. Executive Summary (2–3 bullets)
- Client's stated pain (from discovery call W5H)
- Omerion's recommended approach (1–2 sentences, non-technical)
- Expected outcomes (e.g., "50% reduction in lead response time", "30% cost reduction through automation")

### 2. W5H Recap (1 page)
| Dimension | What to include |
|-----------|-----------------|
| **What** | The specific problem: lead velocity, team ops, portfolio visibility, deal velocity, etc. |
| **Why** | Impact if unsolved: lost revenue, churn risk, operational cost, competitive disadvantage |
| **Who** | Decision maker(s) and their incentives; stakeholder concerns (tech anxiety, budget constraints) |
| **When** | Urgency timeline; Q2 budget cycle / next renewal window / "now" |
| **Where** | Industry context: competitive landscape, tech adoption in the client's sector, existing tool stack |
| **How** | High-level approach without naming internal Omerion systems (DAAM/ORIA/etc.) |

### 3. Service Package Selection

Recommend **one** primary package:

| Package | Best for | Includes | Price band |
|---------|----------|----------|------------|
| **Revenue Acceleration Engine** | Revenue leaders / small sales teams (1–20 people) | AI-powered lead enrichment, ICP scoring, automated outreach sequencing | $2–5k/mo |
| **Ops Intelligence Layer** | Ops leaders / growing orgs (20–100+ people) | Lead velocity + team dashboard, performance benchmarking, hiring readiness signals | $5–15k/mo |
| **Research & Decision Stack** | Founders / analysts needing market intelligence | Competitive research pipelines, data synthesis, strategic alerts, RAG-powered search | $3–10k/mo |
| **Process Automation Suite** | Ops-heavy orgs with repetitive workflows | Workflow automation, doc generation, compliance coordination, approval orchestration | $5–12k/mo |

**Pricing logic**: Use the "expected annual impact × 10% annual fee" rule (e.g., $600k revenue impact = $60k annual fee = $5k/mo). Anchor to problem severity + client budget.

### 4. Demo Plan

- **Demo 1** (30 min, week 1): Live system walkthrough using client's own data (top 5 recent leads, current team, recent deals)
- **Demo 2** (45 min, week 2): Deep-dive on package-specific feature (lead scoring logic, underwriting playbook, close timeline visualization)
- **POC window**: 30–90 days with limited data, track KPIs (response rate lift, deal velocity improvement, time-to-close reduction)

### 5. 30/60/90 Plan

**Week 1–4 (Discovery + Setup)**
- Data connectors: LinkedIn, Apollo/Hunter, Google Sheets, CRM, Zapier/Make
- Custom field mapping for client's workflows
- Team training (1 hr group + 30 min 1:1 per user)

**Week 5–8 (Pilot + Optimization)**
- Live lead enrichment on new inbound
- Weekly check-in on data quality, team adoption, early metrics
- Prompt refinement based on feedback

**Week 9–12 (Scale + Handoff)**
- Full rollout to all users
- Final training + documentation
- Transition to support; success metrics report

### 6. Pricing

Use **value-based pricing** (not seat-based):
- Annual expected impact ÷ 10 = first-year fee (ROI target: 10×)
- Example: $600k new commission revenue → $60k annual fee = $5k/month

**Payment schedule**: 50% upfront, 50% on successful POC completion.

### 7. Success Metrics

Define 2–3 measurable KPIs tied to client's stated pain:

| Pain → KPI | Baseline | Target (90d) | Measurement |
|-----------|----------|--------------|-------------|
| Slow lead response | 48h avg | 4h avg | Timestamp: lead received → first outreach |
| High manual task burden | 10h/week | <4h/week | User survey + time-tracking tool |
| Pipeline conversion rate | 8% close | 12% close | Finance: closed deals ÷ qualified leads |
| Report turnaround time | 5 days | <1 day | Ops log: request → delivery timestamp |

## HITL Gates

Route to founder for final review if:
- Proposal price exceeds $15k/month (executive approval)
- Client is strategic (market-leading firm, press opportunity)
- Scope creep detected (scope > selected package + demo plan)
- Success metrics are vague or unmeasurable

## AI Automation Delivery Notes

- **Avoid tech jargon**: use "AI-powered" not "LLM", "lead ranking" not "RAG", "dashboard" not "frontend"
- **Emphasize time back & revenue**: ops leaders care about hours saved; founders care about revenue lift and cost reduction
- **Anchor in their language**: mirror the client's own words from the W5H when describing outcomes — never reframe using generic SaaS metrics
- **Compliance awareness**: for finance, healthcare, or legal clients, note "We adhere to your data handling policies and can support GDPR/HIPAA-compatible configurations"

## References

- `agents/offer_matching/state.py` — proposal state schema
- `agents/offer_matching/prompts.py` — proposal drafting system prompt
