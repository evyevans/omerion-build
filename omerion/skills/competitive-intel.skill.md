---
name: competitive-intel
tier: B
agent_number: 18
graph: agents.competitive_intel.graph:build
schedule: "0 9 * * 1,3,5"
trigger: cron
triggers:
  - cron
  - webhook:discord.compete
events_consumed: []
events_emitted:
  - competitor.signal.indexed
  - rd.insights.batch.ready
hitl: false
discord_channel: compete
---

# Competitive Intelligence

Tracks AI-agency / automation-platform competitors via RSS, blog feeds, and
release pages. Triages each signal with Claude Haiku (kind + impact band),
upserts a row into `competitor_battle_cards`, and surfaces high-impact
signals to `r3_strategic_architect` via `rd.insights.batch.ready`.

Runs Mon/Wed/Fri at 09:00 America/Toronto via APScheduler.

## Status: skeleton

`fetch_signals()` returns `[]` until competitor sources are configured in
`omerion/config/agents.yaml :: competitive_intel.competitors`. The graph,
analysis, persistence, and event emission are live.
