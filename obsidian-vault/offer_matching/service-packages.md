# Offer Matching — Service Package Definitions

Last updated: 2026-06-04
Maintained by: PAIR (offer_matching, Agent #7)

Four service packages. Every proposal maps exactly one contact to exactly one package.
Package → demo pairing is enforced by `_validate_package_demo_pair()` in tools.py.

## revenue_acceleration_engine

**Demo:** DAAM
**Target archetype:** high_velocity (revenue_leader)
**Core problem:** Speed-to-lead > 60s; manual outreach; pipeline velocity too slow
**What Omerion delivers:** Webhook-triggered lead capture → AI personalisation → HITL-approved send → attribution
**Typical timeline:** 6–8 weeks
**Price band:** $4,500–$6,500/month retainer
**30/60/90:** P1: webhook ingress + routing POC; P2: full sequence + one client onboarded; P3: attribution report + case study

## ops_intelligence_layer

**Demo:** CAPA
**Target archetype:** system_multiplier (ops_leader)
**Core problem:** Manual reporting cycles consuming team hours; no unified ops intelligence view
**What Omerion delivers:** Data pipeline → synthesis → recurring structured brief → Slack/email delivery
**Typical timeline:** 4–6 weeks
**Price band:** $3,500–$5,500/month retainer
**30/60/90:** P1: data source integration + first automated report; P2: full brief cadence + HITL gate; P3: optimisation + handoff

## research_decision_stack

**Demo:** REMI
**Target archetype:** capital_allocator (professional_services_owner)
**Core problem:** Research taking too long; manual competitive intelligence; slow strategic decisions
**What Omerion delivers:** Signal ingestion → semantic clustering → weekly founder-ready intelligence brief
**Typical timeline:** 4–6 weeks
**Price band:** $3,500–$5,000/month retainer
**30/60/90:** P1: feed setup + first brief; P2: full taxonomy + founder calibration; P3: competitive alert layer + handoff

## process_automation_suite

**Demo:** ASAP
**Target archetype:** system_multiplier or sme_founder
**Core problem:** Repetitive manual workflows eating time; can't scale without hiring
**What Omerion delivers:** Process mapping → automation build → HITL gates → self-sufficiency training
**Typical timeline:** 8–10 weeks
**Price band:** $4,000–$6,000/month retainer
**30/60/90:** P1: process audit + first workflow automated; P2: full suite + client trained; P3: handoff + documentation

## Hard Rules

- One package per proposal. NEVER propose two packages to one contact.
- NEVER include dollar amounts in the founder memo — price_band is internal only.
- Demo must match package exactly per `_validate_package_demo_pair()`.
- Blueprint phases must use business outcomes, not technical deliverables.
