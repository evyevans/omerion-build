# Auditor Weekly Report Rubric

Last updated: 2026-06-04
Maintained by: GUARD (auditor, Agent #19)

Published every Monday. Audience: founder. Format: Markdown. Max 800 words.
Tone: factual, terse, action-oriented. No praise, no hedging.

## Required Sections (in order)

### 1. Health Score (1 line)
"Fleet health this week: X/100. Delta from last week: +/−Y."
Formula: `100 − (CRITICAL_count × 20) − (SUSPICIOUS_count × 5) − (unresolved_escalations × 10)`

### 2. Critical Violations (table or "None this week")
| agent | rule # | action taken | timestamp |

### 3. Suspicious Patterns (bullets, max 5)
Format: `{agent} — {pattern type} — {signal observed} — {recommended watch}`
If more than 5: "N additional low-signal patterns — see audit_log for full list."

### 4. Agent Leaderboard (always present)
"Top offending agents this week:"
| rank | agent | critical | suspicious | clean | fix_applied |
Source: `auditor_verdicts.source_agent` column (migration 0052).

### 5. Cost Summary
| agent | avg_cost_usd | max_cost_usd | ceiling | status |
Flag any agent within 20% of ceiling as ⚠️.

### 6. Recommended Actions (max 3 bullets)
Format: `{specific action} — owner: {founder/healer/trainer} — urgency: {now/this week/monitor}`

## Guardrails

- NEVER include raw SQL or file diffs in the report.
- NEVER name internal codenames (DAAM/CAPA/REMI/ASAP) — use "service package" generically.
- NEVER speculate on root causes not evidenced in audit_log.
- DO link to specific audit_log IDs for every violation referenced.
- DO use exact numbers — never "approximately" or "around".
- DO surface health score delta prominently — founders track trend, not absolute score.
