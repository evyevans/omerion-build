# LinkedIn Outreach — Messaging Guardrails

Last updated: 2026-06-04
Maintained by: REACH (linkedin_outreach, Agent #4)

Platform rules, tone standards, and feedback loop criteria.

## Character Limits

| Message type | Hard limit | Aim for |
|-------------|-----------|--------|
| Connection note | 300 chars (LinkedIn hard limit) | ≤ 250 |
| Direct message | 1,000 chars | ≤ 500 |

## Tone: Absolute Dos and Don'ts

**DO:**
- Reference one specific, verifiable signal about THIS contact (not their industry — this person)
- One clear call to action per message — never two
- Short paragraphs: 2–3 sentences max per block
- Peer-to-peer "I" voice — not "our team at Omerion"

**NEVER:**
- "I wanted to reach out" — immediate credibility drop
- "Hope this finds you well" / "Hope you're having a great day"
- "Just checking in" / "Following up on my previous message"
- Multiple CTAs in one message
- Generic industry claims without a specific company hook
- Pricing, package names, or internal codenames (DAAM/CAPA/REMI/ASAP) in any outreach

## Double-Send Guard

Before queuing any send, check `outbound_communications` for an existing row with
`contact_id + template_key` combination:
- If `status in [sent, queued_for_sender]`: skip silently — do NOT re-queue
- `upsert(on_conflict=idempotency_key, ignore_duplicates=True)` preserves the existing status

## outreach_signals Feedback Loop Write Criteria

A sent message qualifies for indexing into `outreach_signals` when:
- `reply_received = true` AND
- `days_to_reply ≤ 3` AND
- Contact advanced to warm or evaluation stage after the reply

Low-performing sends (no reply after 14 days OR opt-out received) are NEVER indexed.
This keeps the playbook namespace clean — only winning angles feed future drafts.
