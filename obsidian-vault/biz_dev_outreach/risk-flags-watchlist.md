# Biz Dev Outreach — Risk Flags Watchlist

Last updated: 2026-06-04
Maintained by: SEEK (biz_dev_outreach, Agent #15)

10 deterministic flags. Any flag present → HITL card generated before send.
Multiple flags on one application = escalated review priority (surfaced first in the card).

## Flag Definitions

| Flag | Trigger condition | Source |
|------|-----------------|--------|
| `low_rank_score` | rank_score < platform tier minimum (7.5/7.0/6.5) | rank-system-rubric formula |
| `missing_budget` | No budget stated AND budget_score = 0 | posting parse |
| `scam_signals` | missing_budget + description < 300 chars + company not on LinkedIn | 3 co-occurring signals |
| `skill_mismatch` | domain_match < 5.0 | rank sub-score |
| `short_deadline` | Posting close date < 7 days from today | posting.deadline field |
| `duplicate_company` | Application to same company domain < 30 days ago | job_applications table lookup |
| `forbidden_keywords` | Posting contains: "W2 employee required", "equity only", "revenue share only", "unpaid" | keyword scan |
| `identical_cover_text` | Jaccard similarity > 0.70 vs any prior application cover text | job_applications table |
| `vague_scope` | Posting body length < 300 characters | len(posting.description) |
| `off_brand_voice` | style_guard score < 0.60 on drafted application | style_guard check post-draft |

## HITL Card Format

Each flagged application appears as a Discord card with checkboxes:
```
⚠️ SEEK Risk Flags — {N} flag(s) detected

Application: {title} at {company} ({platform})
Rank score: {score} | Deadline: {date}

Flags:
[ ] low_rank_score: 6.8 (threshold: 7.0 for Tier A)
[ ] short_deadline: closes in 5 days

[APPROVE — send application]  [REJECT — skip]  [EDIT — return to queue]
```

## Flag Escalation Priority

Applications with ≥ 3 flags → surface at top of HITL queue with "HIGH RISK" label.
Applications with scam_signals flag → always surface first regardless of other flags.
Applications with 0 flags → no card generated; queue directly for send.
