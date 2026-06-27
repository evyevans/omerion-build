# Contact Persona Classification Guide

Last updated: 2026-06-03
Maintained by: FIND (lead_scraper_enricher, Agent #3)

## Persona Tokens

FIND issues one Haiku call per contact to assign a single persona token.
Output must be exactly one token from this list. No prose, no punctuation.

| Token | Target Title Patterns | When to assign |
|-------|----------------------|---------------|
| `ops_leader` | Head of Operations, COO, Chief of Staff, Operations Manager, VP Operations | Default for any operations-titled role |
| `revenue_leader` | VP Sales, Director of Growth, CRO, Revenue Lead, Head of Business Development | Any sales/growth ownership title |
| `sme_founder` | Founder, Co-Founder, CEO (company < 50 employees) | Founder at a small/medium business |
| `agency_owner` | Owner, Managing Director, Principal (at an agency/consultancy) | Ownership role at a services firm |
| `saas_founder` | CTO-turned-CEO, Technical Co-Founder, Founder (SaaS product company) | Technical founder shipping a product |
| `professional_services_owner` | Managing Partner, Principal (law/accounting/advisory) | Ownership at a regulated professional services firm |
| `ecommerce_operator` | Founder/GM (ecom), Director of Ecommerce, Head of DTC | Any role owning an online store or DTC brand |
| `hr_talent_leader` | VP People, Head of Talent, Recruiting Lead, CHRO | HR/People-focused role |
| `finance_ops` | CFO, Finance Manager, Controller, Head of Finance | Finance ownership title |
| `unknown` | Cannot determine from available signals | Use as last resort only |

## Classification Rules

1. When a title maps to multiple tokens (e.g. "Founder & COO"), prefer `sme_founder` if team < 20, `ops_leader` if team >= 20.
2. "CEO" at a company with < 50 employees → `sme_founder`.
3. "CEO" at a company with > 200 employees → `revenue_leader` (acts as buyer, not founder).
4. If no title is available but LinkedIn bio mentions "built" or "started" → `sme_founder`.
5. Never guess from the company domain alone — use `unknown` if title is absent.
