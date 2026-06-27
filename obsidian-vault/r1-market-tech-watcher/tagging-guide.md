# Tagging Guide — TRACK (R1 Market/Tech Watcher)

**Maintained by:** TRACK (r1_market_tech_watcher, Agent #11)  
**Last updated:** 2026-06-03  
**Purpose:** Decision guide for assigning `impact_tag` and `estimated_priority` to
raw RSS signals. These rules are the canonical source for TAG_SYSTEM (prompts.py).

---

## The Five-Tag Taxonomy

Each signal receives exactly ONE `impact_tag`. Choose the DOMINANT service package
most directly affected. When a signal touches multiple categories, assign the tag
whose service package benefit is most specific and actionable.

---

### `daam` — Revenue Acceleration Engine

**Tag when the signal covers:**
- CRM automation and pipeline velocity
- Speed-to-lead infrastructure
- AI SDR / AI-powered outreach
- Lead routing and qualification automation
- Follow-up sequence orchestration
- Sales rep time recovery tools targeting revenue leaders

**Representative keyword clusters:**
`speed-to-lead`, `outreach velocity`, `AI SDR`, `CRM automation`, `pipeline AI`,
`lead qualification`, `follow-up automation`, `sales intelligence`, `revenue AI`

**Example high-signal:** "Acme raises $25M for AI that books qualified meetings
within 60 seconds of lead form submission." → `daam`, `high` (competitive threat)

---

### `capa` — Ops Intelligence Layer

**Tag when the signal covers:**
- Ops workflow automation and reporting
- Executive productivity and time recovery
- Process intelligence and performance dashboards
- Admin AI for operations teams
- Voice-of-customer synthesis tools

**Representative keyword clusters:**
`ops intelligence`, `workflow automation`, `executive productivity`, `reporting AI`,
`process automation`, `operations dashboard`, `admin automation`, `team performance`

**Example high-signal:** "LangGraph 0.3 adds native human-in-the-loop for approval
workflows, reducing ops review time by 40% in beta." → `capa`, `high` (internal_os
also relevant but CAPA is the direct package beneficiary)

---

### `remi` — Research Decision Stack

**Tag when the signal covers:**
- Market intelligence and research synthesis tools
- Real estate AI and proptech platforms
- Capital allocation decision automation
- Investment research pipelines
- Strategic data platforms for professional services

**Representative keyword clusters:**
`proptech`, `real estate AI`, `CRE intelligence`, `market research AI`,
`investment decision`, `capital allocation`, `data synthesis`, `research automation`

**Note:** remi is the narrowest tag — only use it when the signal has explicit
real estate / proptech / investment research relevance. General "market research"
articles without this context should be tagged `capa` or `asap`.

---

### `asap` — Process Automation Suite

**Tag when the signal covers:**
- Document generation and templating automation
- Workflow orchestration and approval chains
- Compliance and accountability automation
- Appointment and scheduling automation
- Multi-step process management

**Representative keyword clusters:**
`document automation`, `workflow orchestration`, `compliance AI`, `approval workflow`,
`process automation`, `scheduling automation`, `contract generation`, `doc gen`

---

### `internal_os` — Internal Platform Improvements

**Tag when the signal covers:**
- LangGraph, Claude API, or Anthropic releases that directly change how Omerion
  builds or runs agents
- MCP protocol updates or new MCP server patterns
- RAG architecture improvements applicable to the agent fleet
- Pinecone, Supabase, or vector database changes affecting Omerion's infrastructure
- Agent orchestration patterns with direct applicability

**Hard constraint:** `internal_os` is NOT a catch-all for general AI news.
Tag only when the signal directly affects HOW OMERION BUILDS or RUNS its agents.
General AI industry trends that don't touch Omerion's stack = `low` priority in
whichever package tag fits best.

---

## Conflict Resolution (Multi-Tag Signals)

When a signal touches multiple categories, apply this priority order:

1. **Competitive threat overrides everything.** If a product launch directly competes
   with one of Omerion's service packages, tag the threatened package. Ignore secondary
   tags.

2. **Most specific tag wins.** `remi` is more specific than `capa`; `daam` is more
   specific than `asap` for outbound-related signals.

3. **Immediate applicability wins.** If a tool can be integrated into Omerion's
   live agent fleet within 30 days, tag `internal_os` even if there's a secondary
   package relevance.

---

## When to Escalate to `high`

Priority escalation requires at least ONE of:
- Direct competitive threat with >$10M funding targeting Omerion's ICP
- Immediate adoption candidate improving a service package within 30 days
- Anthropic or LangGraph release that changes core agent behavior (breaking change OR
  major new capability)

**PROVE confirmation escalation:** If R3 (SHAPE) has submitted a proposal citing
an `estimated_priority = "medium"` signal, and a subsequent PROVE attribution report
confirms the underlying KPI pain is real, the next R1 run on the same topic area
should escalate to `high`. This is a manual process — PROVE does not retroactively
update existing `rd_insights` rows.
