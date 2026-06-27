# Offer Matching — Proposal Memo Rubric

Last updated: 2026-06-04
Maintained by: PAIR (offer_matching, Agent #7)

Guardrails for the Opus-generated founder memo inside each proposal.
The memo is what the founder reads before approving or rejecting the offer at the HITL gate.

## Required Elements (every memo)

1. **Contact anchor (1 sentence):** Full name, title, company, and the ONE pain signal that drove this package match. Must be from research_dossiers or contact_activity_log — not inferred.

2. **Package rationale (2–3 sentences):** Why this package over the other three. Must reference the contact's archetype and at least one concrete metric or signal.

3. **30/60/90 preview (3 bullets):** Business outcomes, not technical deliverables. Each bullet: what the contact will be able to do or measure that they can't today.

4. **Recommended ask (1 sentence):** "Recommend: discovery call this week." or "Recommend: send demo deck via LinkedIn."

## Tone Rules

- **Peer-to-peer.** Write as if the founder wrote it themselves after researching the contact.
- **Specific over generic.** NEVER: "This contact would benefit from automation." ALWAYS: "Sarah's team runs 3 manual reporting cycles per week — CAPA eliminates all three."
- **Confident, not tentative.** No "might", "could possibly", "perhaps", "it seems."
- **Max 200 words total.**

## Hard Guardrails

- NEVER include price ranges or dollar amounts in the memo (price_band is internal).
- NEVER use internal demo codenames (DAAM/CAPA/REMI/ASAP) — use service package names.
- NEVER fabricate contact signals — only cite data from research_dossiers and contact_activity_log.
- NEVER propose two packages in one memo.
- DO ground every 30/60/90 bullet in this specific contact's stated pain, not a generic template.
- DO ensure the 30/60/90 phases match the package definition in service-packages.md.
