# OMERION Backend Agent Inventory

_Single source of truth. Generated 2026-05-18 during the strategic realignment._

The dashboard (`dashboard/src/data/agents.ts`) renders the agents below. Anything in the dashboard that is **not** in this table is a `planned` UI placeholder, not a live backend.

## Live backend agents (15)

| # | Skill (kebab) | Module | Discord channel | Department | HITL | Trigger | Notes |
|---|---|---|---|---|---|---|---|
| 1 | `market-mapper` | `agents.market_mapper` | `#map` | Intelligence | No | cron + Discord | Classifies companies into 9-persona taxonomy. Resurrected in Phase 2. |
| 2 | `hq-lead-scraping` | `agents.high_quality_lead_scraping` | `#leads` | Revenue / Lead Gen | Yes | Discord | Deep research dossiers per priority account. |
| 3 | `lead-scraper` | `agents.lead_scraper_enricher` | `#scout` | Revenue / Lead Gen | No | event + Discord | Contact discovery + persona enrichment. |
| 4 | `icp-scoring` | `agents.icp_scoring` | `#score` | Revenue / Lead Gen | No | event + Discord | Fit × Intent × Timing scoring. |
| 5 | `linkedin-outreach` | `agents.linkedin_outreach` | `#reach` | Revenue / Lead Gen | Yes | event + Discord | LinkedIn-only today. |
| 6 | `crm-nurture` | `agents.crm_nurture` | `#nurture` | Revenue / Lead Gen | Yes | event + Discord | Email + SMS nurture sequences. |
| 7 | `offer-matching` | `agents.offer_matching` | `#match` | Delivery | Yes | event + Discord | Pairs hot contacts to offer packages. |
| 8 | `meeting-intel` | `agents.meeting_intelligence` | `#intel` | Delivery | Yes | Fireflies webhook | Transcripts → W5H + blueprint. |
| 9 | `build-orchestrator` | `agents.build_orchestrator` | `#orch` | Factory | Yes (deploy) | event + Discord | Blueprint → deployment + task tracking. |
| 10 | `outcome-attribution` | `agents.outcome_attribution` | `#attrib` | Delivery | No | event + Discord | Maps closed wins back to agent actions. |
| 11 | `market-watcher` (R1) | `agents.r1_market_tech_watcher` | `#watch` | Intelligence | No | cron + Discord | RSS → tagged R&D insights. |
| 12 | `oss-scout` (R2) | `agents.r2_oss_scout` | `#oss` | Intelligence | No | event + Discord | OSS releases + integration eval. |
| 13 | `strategic-arch` (R3) | `agents.r3_strategic_architect` | `#arch` | Intelligence / RSI | Yes | event + Discord | R&D proposal synthesis. **Phase 1: migrated to `interrupt()`.** |
| 14 | `eval-telemetry` (R4) | `agents.r4_evaluation_telemetry` | `#eval` | RSI | No | cron + Discord | Regression detection + alerts. |
| 15 | `biz-dev-outreach` | `agents.biz_dev_outreach` | `#biz` | Revenue / Biz Dev | Yes | cron + Discord | **Renamed from `job_seeker` in Phase 2.** Finds consulting clients via Contra/Upwork/etc. |

## Planned (skeleton in Phase 5)

| # | Skill (kebab) | Discord channel | Department | Status |
|---|---|---|---|---|
| 16 | `client-onboarding` | `#onboard` | Delivery | Phase 5.1 |
| 17 | `client-success` | `#success` | Delivery | Phase 5.2 |
| 18 | `competitive-intel` | `#compete` | Intelligence | Phase 5.3 |

## Dashboard-only fictional cards (no backend, render dimmed)

ARIA, FORGE, SCOUT-AF, GATEKEEPER, PATCHER, LIBRARIAN, ANALYST, COMPETITOR (covered by `competitive-intel` Phase 5), ONBOARDING (covered by `client-onboarding` Phase 5), SUCCESS_OPS (covered by `client-success` Phase 5), PROMPT_OPTIMIZER, RAG_AUDITOR, TOKEN_OPTIMIZER, SYNTHESIS.

These will render with `planned: true` in `dashboard/src/data/agents.ts` until they have backend code + Discord routing.

## Read-only Discord channels

`#founder-hitl` (approve/reject UI surface) and `#mission-control` (aggregator) map to `None` in `CHANNEL_SKILL_MAP`. They never trigger an agent run.
