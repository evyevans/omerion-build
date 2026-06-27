# CRM Nurture — Cooldown Gating Policy

Last updated: 2026-06-04
Maintained by: GROW (crm_nurture, Agent #5)

Controls when a contact is eligible for the next email in their nurture sequence.

## Default Cooldown Per Stage

| Stage | Default cooldown | Rationale |
|-------|----------------|-----------|
| awareness | 7 days | Building familiarity; too-frequent contact signals spam |
| consideration | 5 days | Warming up; cadence can tighten |
| evaluation | 3 days | Active evaluation window; high intent signals |
| decision | 2 days | Decision imminent; maintain momentum without pressure |
| post-demo | 1 day | Immediate follow-through expected; window is short |

## Engagement Override Rules

These conditions reduce or skip the cooldown:

| Condition | Override |
|-----------|---------|
| `last_reply_at < 48 hours ago` | Skip cooldown entirely — contact is actively engaged |
| `last_open_at < 24h AND open_count >= 3 this week` | Reduce cooldown by 50% |
| Contact moved to next stage after last send | Reset cooldown to new stage's default |

## Ghost Escalation

If a contact has received 4+ sends in the current stage with zero replies:
1. Set `contact.nurture_status = "stalled"`
2. Create founder review item: "Contact stalled — {full_name}, {company}, stage: {stage}, sends: {count}"
3. **DO NOT continue sending.** Wait for founder decision.

Stalled contacts idle for 21+ days should be archived (set `status = "inactive"`).
Contacts should never receive an infinite sequence without a human checkpoint.

## Opt-Out Handling

Any reply containing: "unsubscribe", "stop", "remove me", "not interested", "please don't":
1. Set `contact.opt_out = true` immediately
2. Halt all further sends — do NOT queue anything
3. Log opt-out event to contact_activity_log with timestamp
4. NEVER re-add to any nurture sequence without explicit founder re-activation
