# ICP Scoring — Digest Narrative Guidelines

Last updated: 2026-06-04
Maintained by: RATE (icp_scoring, Agent #6)

Structure for the founder-facing weekly digest of scored contacts.
Rendered by `render_digest()` using Sonnet. Max 300 words total.

## Required Structure

**Line 1 — Tier summary:**
"This week: {X} hot, {Y} warm, {Z} watchlist contacts scored."

**Section 2 — Hot contacts (all named):**
One line per contact: `{Full Name} | {Title}, {Company} | Why: {≤15 words} | Action: {one verb phrase}`
The "Why" MUST cite a specific signal — last email snippet, engagement count, or timing event.
NEVER: "Seems like a good fit." ALWAYS: "Replied 3× in 7 days, wrote: 'manual ops killing us'."

**Section 3 — Warm contacts (grouped):**
"{Y} warm contacts. Top 3 to watch: {Name, Company, one-line angle}"
Do not name every warm contact — digest length is bounded.

**Section 4 — Recommended next actions (max 3 bullets):**
- Specific, named: "Book discovery with {Name} this week — timing score 1.0"
- Or segmented: "Trigger REACH cold sequence for {N} ops_leader contacts in watchlist"

## Tone Rules

- Factual, peer-to-peer, action-oriented.
- No hedging: not "might be worth reaching out" — "reach out this week."
- No raw scores in the digest — use tier labels (hot/warm/watchlist) only.
- Every named contact must include title and company.

## Guardrails

- NEVER fabricate contact email content — only cite signals from contact_activity_log.
- NEVER include pricing, package names, or internal codenames (DAAM/CAPA/REMI/ASAP).
- DO surface timing score when it is 1.0 — it is a strong urgency signal for the founder.
- If zero hot contacts this week: "No hot contacts this week. {Y} warm contacts on deck." Do not pad.
