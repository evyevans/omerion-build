# CRM Nurture — Email Style Guide

Last updated: 2026-06-04
Maintained by: GROW (crm_nurture, Agent #5)

Style guardrails applied to every draft before it reaches the HITL gate.
These rules are enforced by style_guard in tools.py — this file is the human-readable source.

## Hard Limits

| Rule | Limit |
|------|-------|
| Body word count | ≤ 130 words |
| Subject line length | ≤ 8 words |
| Subject line — question marks | 0 (never use) |
| Paragraphs per email | Max 3 |
| Sentences per paragraph | Max 3 |
| CTAs per email | Exactly 1 |
| Exclamation marks | Max 1 per email |

## Forbidden Phrases (any match = draft rejected)

- "I wanted to reach out"
- "Hope this finds you well" / "Hope you're having a great week"
- "Just checking in" / "Just following up"
- "Following up on my last email" / "As per my previous email"
- "I thought you might be interested"
- "Please don't hesitate to"
- "Let me know if you have any questions"
- "Synergy" / "game-changer" / "revolutionary" / "disruptive" / "innovative solution"
- "Our platform" / "our solution" / "we offer" / "we provide"

## Tone Requirements

**Specific:** Reference THIS contact's company, role, or a signal from their contact record. Generic emails are invisible.

**One ask:** The CTA must be one explicit request — "15-minute call this week?" or "Worth a quick look?" — never two options.

**Peer voice:** Write as if the founder sent it personally. "I" not "we". First name in the opener if known.

**Short is better:** If you can say it in 5 words, use 5 words. Padding signals low effort.

## Subject Line Rules

- Max 8 words, no question marks
- Specific > clever: "Your reporting workflow, automated" beats "Transform your business"
- Never start with "Re:" unless it is a genuine reply thread
- Never use ALL CAPS or excessive punctuation
