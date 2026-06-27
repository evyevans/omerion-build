---
name: lead-scoring
tier: A
agent_number: null
description: ICP scoring matrix for general business prospects (ops leaders, founders, revenue teams, etc.)
triggers:
  - event:contact.enriched
  - manual
hitl: false
---

# Lead Scoring Matrix

ICP (Ideal Customer Profile) scoring for general business personas. Outputs a 1–100 fit score used by MATCH to prioritize outreach and by NURTURE to customize messaging.

## Scoring Dimensions

| Dimension | Weight | High Signal (25 pts) | Medium (15 pts) | Low (5 pts) |
|-----------|--------|----------------------|-----------------|-----------|
| **Persona** | 30% | SME Founder, Ops Leader, Revenue Leader (direct buyer authority) | Agency Owner, SaaS Founder, Finance Ops (project-level authority) | HR/Talent Leader, unknown, NEEDS_REVIEW |
| **Industry** | 20% | SaaS, fintech, marketing agency, e-commerce, consulting (tech-forward) | Professional services, healthcare, manufacturing (moderate fit) | Government, nonprofit, regulated utilities (low ROI) |
| **Recent Activity** | 20% | Hiring ops/automation roles; recent funding; launched new product | Stable growth; 1–3 open roles; LinkedIn active in last 30d | Flat headcount; no recent news; dormant social signals |
| **LinkedIn Engagement** | 15% | 500+ connections; active posts/shares; recent profile update | 100–500 connections; occasional engagement | <100 connections; no recent activity |
| **Firmographic** | 15% | 10–200 employees (SMB sweet spot); $1M–$50M revenue signal | 200–500 employees or <10 (scaling or micro-team) | Enterprise (500+) or pre-revenue startup |

## Calculation

```
score = (persona_pts * 0.30) + (industry_pts * 0.20) + (activity_pts * 0.20) + 
        (linkedin_pts * 0.15) + (firm_pts * 0.15)

Ranges:
  90+   = immediate outreach (warm, MATCH tier 1)
  75–89 = nurture sequence (REACH 4-touch)
  60–74 = backlog for future (NURTURE monthly digest)
  <60   = suppress (do not contact)
```

## General Rules

- **If ops_leader or sme_founder + hiring automation/ops roles + tech-forward industry**: boost +10 pts (active spend signal)
- **If revenue_leader + pipeline or CRM pain signals + growth-stage company**: priority (revenue pressure = urgent buyer)
- **If any persona + flat headcount 24m+ + no recent news**: reduce −10 pts (stagnant accounts churn faster)

## HITL Override

Founder can manually override score ±25 pts if prospect matches a specific campaign or known opportunity.

## References

- `agents/icp_scoring/state.py` — score state schema
- `agents/icp_scoring/prompts.py` — scoring decision system prompt
- `omerion_core/fit_score.py` — deterministic scoring functions
