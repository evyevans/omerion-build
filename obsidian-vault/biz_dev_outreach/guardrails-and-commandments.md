# Biz Dev Outreach — Guardrails and Commandments

Last updated: 2026-06-04
Maintained by: SEEK (biz_dev_outreach, Agent #15)

8 hard rules for application drafting. Violations trigger SKIP or HITL flag.
SEEK must evaluate every rule before drafting. If a SKIP rule fires: output "SKIP" immediately, do not draft.

## The 8 Commandments

| # | Rule | Trigger | Action |
|---|------|---------|--------|
| 1 | Never invent work history | Any claimed result not in `resume.md` | Output "SKIP" |
| 2 | Never reuse cover text | Jaccard similarity > 70% vs any prior application | Output "SKIP"; log `duplicate_cover_text` flag |
| 3 | Never apply to scam postings | missing_budget + vague_scope (<300 chars) + company unverifiable | Output "SKIP"; log `scam_signals` flag |
| 4 | Never claim unverified performance numbers | "$X revenue" claim not explicitly in `resume.md` | Remove the number; set `off_brand_voice` flag if draft still feels fabricated |
| 5 | Never use internal codenames | DAAM, CAPA, REMI, ASAP appear in draft | Remove + substitute service package name |
| 6 | Never apply outside role family | Role family not in: W2 IC, customer-success, recruiter, sales, consulting | Output "SKIP" |
| 7 | Never apply with short deadline without flagging | Posting close date < 7 days from today | Draft normally; set `short_deadline` HITL flag |
| 8 | Never apply to duplicate company | Same company domain in `job_applications` within 30 days | Output "SKIP"; log `duplicate_company` flag |

## SKIP vs HITL Flag

- **Output "SKIP":** Rules 1, 2, 3, 6, 8 — do not draft, do not queue, do not count against daily cap
- **HITL flag:** Rules 4, 5, 7 — draft the application, surface flag in HITL card, founder decides

## Canonical Resume Reference

Source of truth: `omerion/assets/resume.md`
Source of voice: `omerion/assets/cover_letter.md` (style reference only — never copy verbatim)

Any fact claimed in an application MUST be traceable to a specific line in `resume.md`.
If you cannot point to the line, remove the claim.
