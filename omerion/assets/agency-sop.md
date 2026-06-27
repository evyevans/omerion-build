# omerion.io — Agency SOP Framework
### Talent Identification, Delegation, and Delivery Operations

**Version:** 1.0
**Owner:** Evykynn Panton, Founder — omerion.io
**Classification:** Internal — Confidential
**Last Updated:** 2026-05-06

---

# SECTION 1: Agency Blueprint Overview

## What Is the Blueprint?

The **blueprint** is the scoped, client-approved proposal that defines every deliverable, integration, timeline, and system component for a given engagement. It is the binding contract between omerion.io and the client before any work begins.

The blueprint is not a pitch deck. It is an operational specification — a decision document that answers:
- What will be built or deployed
- What existing omerion.io infrastructure will be used (vs. what is new)
- Who owns each deliverable
- What the client's success criteria are
- What is explicitly out of scope

The blueprint exists to prevent scope creep, protect the client's vision, and protect Evykynn's systems from unauthorized modification.

## Blueprint Approval Gate

**No talent is hired. No code is written. No system is modified before a blueprint is signed.**

The approval gate requires:

- [ ] Client has reviewed and signed the blueprint document
- [ ] All deliverables are itemized with acceptance criteria
- [ ] Integrations are listed with the client's specific platforms confirmed
- [ ] Timeline and milestone structure is agreed upon
- [ ] Evykynn has reviewed the blueprint against the omerion.io stack — no new tooling is introduced without her sign-off
- [ ] Payment terms and support SLA are confirmed

## End-to-End Delivery Model

```
Blueprint Approval
      ↓
Role Identification (which specialist roles does this blueprint require?)
      ↓
Talent Assembly (source, vet, contract)
      ↓
Task Brief Distribution (written, scoped briefs — no verbal handoffs)
      ↓
Execution (sprint-based, async-first, GitHub + Discord)
      ↓
Internal QA (QA Specialist validates against acceptance criteria)
      ↓
Client UAT (client tests against their own success criteria)
      ↓
Deployment Sign-off (Evykynn approves final push to production)
      ↓
Handoff (documentation, client training, support SLA activated)
```

## Vision Alignment Mandate

All hired talent operates under one non-negotiable rule:

**You extend, integrate, and deploy what exists. You do not rebuild, redesign, or substitute.**

Talent does not make architectural decisions. Talent does not introduce new tools, frameworks, libraries, or services without explicit written approval from Evykynn. The client's vision and Evykynn's architecture are fixed inputs — talent is the execution layer.

---

# SECTION 2: Talent Identification Framework

The following roles are the standard specialist categories used across omerion.io client engagements. Not every engagement requires all ten roles. Blueprint scope determines which roles are activated.

---

## Role 1: AI Automation Engineer

**a) Role Title:** AI Automation Engineer

**b) Trigger Condition:** Activated when a blueprint includes workflow automation, multi-step API orchestration, or event-driven process automation that does not require LangGraph agent architecture. Typically engaged in Phase 1 (integration) or Phase 2 (automation buildout).

**c) Core Responsibilities:**
- Build and configure automation workflows using Python scripts and the Claude-native agent OS
- Write Python scripts for API integrations, data transforms, and webhook handlers
- Connect client data sources (CRM, Apollo/Hunter, Google Workspace) to omerion.io backend endpoints
- Maintain and extend existing automation flows — no new tooling unless Evykynn approves

**d) NOT Allowed To:**
- Modify omerion.io LangGraph agent graphs or state schemas
- Replace existing automation workflows with alternative platforms
- Access Supabase directly without backend developer oversight
- Make direct changes to production environments without QA sign-off

**e) Key Skills and Tools:**
- Python (intermediate to advanced — async, API integration, webhook handling)
- Claude API (understanding agent OS architecture and prompt patterns)
- Webhook architecture, OAuth flows, RapidAPI integrations
- GitHub (version-controlled workflow exports)

**f) Preferred Sourcing Platform:** Upwork (filter: Python automation, Claude/LLM API specialists)

**g) Compensation Model:** Project-based per blueprint phase. Hourly is acceptable for open-ended integration work where scope is hard to bound upfront. Typical range: $40–$85/hr or fixed per deliverable.

---

## Role 2: Backend Developer

**a) Role Title:** Backend Developer

**b) Trigger Condition:** Activated when a blueprint requires new API endpoints, database schema extensions, webhook receivers, or backend service integrations. Typically Phase 1 or Phase 2.

**c) Core Responsibilities:**
- Build FastAPI endpoints that extend the omerion.io backend
- Write Supabase migrations (`IF NOT EXISTS` mandatory on all DDL)
- Implement webhook receivers and event emitters that conform to `omerion_core/events/bus.py` patterns
- Integrate third-party APIs (Stripe, Twilio, ElevenLabs, RapidAPI) into existing service boundaries

**d) NOT Allowed To:**
- Alter the `omerion_core/events/` event schema or `EventType` enum without Evykynn's explicit approval
- Modify HITL routing logic in `omerion_core/hitl/`
- Write to `.env`, `config/agents.yaml`, or any production config file
- Drop or alter existing Supabase columns — extension only

**e) Key Skills and Tools:**
- Python, FastAPI, async SQLAlchemy / asyncpg
- Supabase (PostgreSQL, Row Level Security, migrations)
- REST API design, webhook security (HMAC validation)
- GitHub (PRs with full diff review required before merge)

**f) Preferred Sourcing Platform:** Toptal or Upwork (filter: FastAPI + Supabase, Python backend)

**g) Compensation Model:** Project-based per deliverable or retainer for engagements with ongoing backend support needs. Hourly for change requests. Typical range: $60–$120/hr.

---

## Role 3: Frontend Developer

**a) Role Title:** Frontend Developer

**b) Trigger Condition:** Activated when a blueprint includes a client-facing portal, agent monitoring dashboard, or any UI component. Typically Phase 2 or Phase 3.

**c) Core Responsibilities:**
- Build React (TypeScript) interfaces that conform to omerion.io brand standards
- Connect frontend to Supabase realtime subscriptions and FastAPI endpoints
- Implement pixel-accurate UI from design specs — no freestyle design decisions
- Extend the omerion.io dashboard (`dashboard/`) for client-specific monitoring views

**d) NOT Allowed To:**
- Redesign existing components without design approval from Evykynn
- Introduce new frontend frameworks or component libraries without approval
- Directly call LangGraph or backend agent APIs — all calls go through defined FastAPI endpoints
- Deploy to production without QA sign-off

**e) Key Skills and Tools:**
- React 18, TypeScript, Vite
- Supabase JS client (realtime subscriptions)
- CSS-in-JS or Tailwind (project-standard only)
- Brand palette compliance: #1a1714 background, #c4773a amber, #e8e0d0 cream; Cormorant Garamond + DM Sans

**f) Preferred Sourcing Platform:** Toptal, Upwork, or direct network (React + TypeScript specialists)

**g) Compensation Model:** Project-based per UI milestone. Hourly for iterative refinement. Typical range: $50–$100/hr.

---

## Role 4: Data & Research Specialist

**a) Role Title:** Data & Research Specialist

**b) Trigger Condition:** Activated when a blueprint requires B2B data ingestion, business intelligence pipelines, or multi-source enrichment workflows. Typically Phase 1.

**c) Core Responsibilities:**
- Build data pipelines from Apollo.io, Hunter.io, Clearbit, and Crunchbase into Supabase
- Clean, normalize, and schema-map company and contact data to omerion.io data models
- Configure enrichment API endpoints (Apollo, Hunter, Clearbit, FullContact) and manage rate limits and error handling
- Maintain data freshness schedules aligned to client intelligence cadence

**d) NOT Allowed To:**
- Scrape platforms in violation of their terms of service
- Store raw third-party data beyond what the client's license allows
- Alter existing Pinecone vector schemas or namespaces without Evykynn's approval
- Substitute approved data sources for alternatives without blueprint sign-off

**e) Key Skills and Tools:**
- Python (requests, pandas, asyncio)
- Apollo.io, Hunter.io, Clearbit, FullContact APIs (B2B enrichment and contact discovery)
- Supabase (bulk inserts, upserts, schema-mapped upserts)
- Data pipeline scheduling (APScheduler, Celery + Redis, or cron for lighter cadences)

**f) Preferred Sourcing Platform:** Upwork (filter: B2B data engineering, Apollo/Hunter integration, Python data pipelines)

**g) Compensation Model:** Project-based for initial pipeline build. Retainer for ongoing data maintenance engagements. Typical range: $45–$90/hr.

---

## Role 5: Prompt Engineer / AI Systems Specialist

**a) Role Title:** Prompt Engineer / AI Systems Specialist

**b) Trigger Condition:** Activated when a blueprint requires new LangGraph agent nodes, Claude system prompt design, multi-agent coordination logic, or output quality optimization. Typically Phase 2.

**c) Core Responsibilities:**
- Design and refine system prompts for Claude (Opus, Sonnet, Haiku) per the `ClaudeRouter` model-selection framework
- Build new LangGraph nodes with `@traced_node` decoration and proper state schema adherence
- Implement HITL checkpoints within agent graphs per `omerion_core/hitl/review.py` patterns
- Optimize prompt chains for token efficiency and output consistency

**d) NOT Allowed To:**
- Instantiate `Anthropic()` directly — all LLM calls must go through `ClaudeRouter`
- Modify agent state schemas (`state.py`) without Evykynn's review
- Add new Pinecone namespaces or alter existing vector schemas
- Use OpenAI models in any agent that currently uses Claude without explicit approval

**e) Key Skills and Tools:**
- Claude API (Opus 4, Sonnet 4, Haiku 4 — model-appropriate task routing)
- LangGraph (stateful graphs, conditional edges, interrupt/resume)
- Python (async, Pydantic v2, type annotations)
- Langfuse (tracing — all nodes must emit traces)
- Pinecone (vector retrieval for context injection)

**f) Preferred Sourcing Platform:** Direct network, LinkedIn, or specialized AI talent platforms. This role requires deep familiarity with Claude's API — screen hard.

**g) Compensation Model:** Project-based per agent or feature scope. Hourly for optimization engagements. Typical range: $80–$150/hr. Do not understaff or underpay this role — output quality directly affects client results.

---

## Role 6: CRM Integration Specialist

**a) Role Title:** CRM Integration Specialist

**b) Trigger Condition:** Activated when a client's blueprint requires integration between omerion.io agents and the client's existing CRM system. The client's CRM is the integration target — this role does not touch Evykynn's operational systems.

**c) Core Responsibilities:**
- Integrate omerion.io event outputs (leads, nurture triggers, meeting notes) into the client's CRM: HubSpot, GoHighLevel, Pipedrive, or Salesforce
- Build bidirectional sync where required (CRM → Supabase, Supabase → CRM)
- Map omerion.io data schemas to the client's CRM field structure
- Configure webhook triggers from the client's CRM into omerion.io inbound routes

**d) NOT Allowed To:**
- Access or modify Evykynn's own operational Google Sheets or Google Workspace systems
- Alter omerion.io event schemas to accommodate CRM quirks — adapt the integration layer, not the core
- Use the client's CRM API credentials for any purpose beyond the defined integration scope
- Deploy CRM integrations to production without backend developer review

**e) Key Skills and Tools:**
- HubSpot API, GoHighLevel API, Pipedrive API, Salesforce REST API
- Python or Node.js (webhook handling, OAuth, API pagination)
- Supabase (reading and writing integration sync tables)
- Python or Node.js (webhook handling, OAuth, API pagination)
- Supabase (reading and writing integration sync tables)

**f) Preferred Sourcing Platform:** Upwork (filter: GoHighLevel developer, HubSpot API, Salesforce integration)

**g) Compensation Model:** Project-based per CRM integration scope. Typical range: $45–$90/hr. Retain on a month-to-month basis if the client's CRM requires ongoing maintenance.

---

## Role 7: DevOps / Cloud Infrastructure Engineer

**a) Role Title:** DevOps / Cloud Infrastructure Engineer

**b) Trigger Condition:** Activated when a blueprint requires VPS provisioning, containerized deployment, CI/CD pipeline setup, or environment configuration. Typically Phase 1 (pre-build) and final deployment (Phase 3).

**c) Core Responsibilities:**
- Provision and configure Hostinger VPS environments for client deployments
- Build and maintain Docker containers and Docker Compose configurations for omerion.io services
- Set up CI/CD pipelines (GitHub Actions) for automated testing and deployment
- Manage Google Cloud services where blueprints include Cloud Storage, Cloud Run, or Pub/Sub
- Configure environment variables, secrets management, and service health monitoring

**d) NOT Allowed To:**
- Write to production `.env` files without Evykynn's explicit sign-off per deployment
- Alter Supabase database credentials or Row Level Security policies
- Modify `config/agents.yaml` — this file is read-only for all non-Evykynn actors
- Spin up new cloud services that are not in the approved blueprint

**e) Key Skills and Tools:**
- Docker, Docker Compose
- Hostinger VPS (Linux, nginx, systemd)
- GitHub Actions (CI/CD pipelines)
- Google Cloud Platform (Cloud Run, Cloud Storage, IAM)
- Redis (Celery broker configuration)

**f) Preferred Sourcing Platform:** Upwork, Toptal, or direct network (DevOps / Linux specialists)

**g) Compensation Model:** Project-based for initial setup. Retainer ($500–$1,500/mo) for ongoing infrastructure maintenance on multi-client deployments. Hourly for one-off changes. Typical range: $60–$120/hr.

---

## Role 8: QA & Testing Specialist

**a) Role Title:** QA & Testing Specialist

**b) Trigger Condition:** Activated at the end of every build phase and before every production deployment. This role is not optional — no client-facing code ships without QA sign-off.

**c) Core Responsibilities:**
- Execute test suites (`python -m pytest omerion/ -x -q`) and validate all tests pass before any PR is merged
- Write integration tests for new agent nodes, API endpoints, and automation workflows
- Validate agentic outputs against acceptance criteria defined in the blueprint
- Perform regression testing on existing functionality after any change
- Document and report defects with reproduction steps, expected vs. actual output, and severity tier

**d) NOT Allowed To:**
- Approve their own work — QA must be independent of the developer who built the feature
- Sign off on a deployment if any P0 or P1 defects are open
- Skip regression testing to hit a deadline
- Modify source code to fix defects — report and escalate to the responsible developer

**e) Key Skills and Tools:**
- Python (pytest, pytest-asyncio)
- REST API testing (Postman, httpx)
- LangGraph trace review via Langfuse
- GitHub (PR review, defect tagging)
- Ability to read and evaluate LLM outputs against qualitative acceptance criteria

**f) Preferred Sourcing Platform:** Upwork (filter: Python QA, API testing, AI/ML output validation)

**g) Compensation Model:** Hourly or per-sprint. Retainer for engagements with ongoing delivery cadence. Typical range: $35–$70/hr. Do not treat this role as optional overhead — missed defects in production cost more than QA ever does.

---

## Role 9: Client Success / Onboarding Specialist

**a) Role Title:** Client Success / Onboarding Specialist

**b) Trigger Condition:** Activated post-deployment, once the system is live and the client is handed the keys. Runs parallel to the final QA phase to prepare training materials.

**c) Core Responsibilities:**
- Conduct client onboarding sessions (live walkthrough of deployed systems)
- Produce client-facing documentation: user guides, FAQ, escalation contacts
- Manage the support SLA period defined in the blueprint (bug reports, usage questions)
- Collect client feedback and route feature requests or defects to Evykynn for prioritization
- Monitor adoption metrics and flag low-engagement signals early

**d) NOT Allowed To:**
- Commit to feature additions or scope changes on Evykynn's behalf
- Grant the client access to internal omerion.io infrastructure or source code
- Override QA or deployment decisions based on client pressure

**e) Key Skills and Tools:**
- Google Workspace (Docs, Sheets, Meet — client communication surface)
- Discord (for clients on the Discord support channel)
- Loom or Zoom (async video walkthroughs)
- Strong written communication — all guidance must be documented, not verbal

**f) Preferred Sourcing Platform:** Direct network, LinkedIn, or Upwork (filter: SaaS onboarding, AI tools customer success)

**g) Compensation Model:** Retainer for ongoing engagements ($800–$2,000/mo depending on client count). Project-based for single-deployment handoffs. Typical range: $25–$60/hr.

---

## Role 10: Project Manager / Delivery Lead

**a) Role Title:** Project Manager / Delivery Lead

**b) Trigger Condition:** Activated at blueprint approval — before any other specialist is engaged. The PM is the first hire and the last to leave an engagement.

**c) Core Responsibilities:**
- Translate the approved blueprint into a sprint-based delivery plan with milestones and task briefs
- Coordinate all specialist roles: assign work, track progress, unblock dependencies
- Own the delivery timeline — escalate to Evykynn the moment a milestone is at risk
- Run weekly milestone reviews and daily async standups (Discord or GitHub)
- Manage scope creep: any request outside the approved blueprint goes to Evykynn before being actioned
- Produce delivery reports for Evykynn's review at each milestone

**d) NOT Allowed To:**
- Approve scope changes or add features without Evykynn's written sign-off
- Grant system access to contractors without Evykynn's approval
- Override QA decisions or pressure the QA Specialist to sign off on failing builds
- Make architectural decisions — escalate to Evykynn

**e) Key Skills and Tools:**
- Project management (async-first, sprint-based, GitHub Issues or Linear for task tracking)
- Strong written communication — all task assignments are written briefs, not verbal
- Discord (team coordination channel management)
- Google Workspace (delivery reports, milestone documentation)
- Familiarity with software delivery in agentic AI or automation contexts

**f) Preferred Sourcing Platform:** Direct network or LinkedIn. This role requires trust — prefer candidates with prior omerion.io engagement history or strong referrals.

**g) Compensation Model:** Retainer for the duration of the engagement ($2,000–$5,000/mo depending on team size and engagement complexity). Never project-based — the PM owns the full delivery arc.

---

# SECTION 3: Delegation Protocols

## Chain of Delegation

```
Evykynn Panton (Founder / Architect)
        ↓
Project Manager / Delivery Lead
        ↓
Specialist Roles (Backend, Frontend, QA, Data, etc.)
```

**No specialist role receives a task directly from Evykynn during active delivery unless the PM is unavailable and the issue is P0.** All delegation flows through the PM. All escalations from specialists flow back up through the PM.

## Delegation Rule Set

- **No verbal handoffs.** Every task delegation is a written, scoped Task Brief (see template below). If it wasn't written down, it wasn't assigned.
- **No assumed context.** Each Task Brief must be self-contained. A specialist reading their brief should need no prior conversation to begin work.
- **No unbounded tasks.** Every brief has a deadline, a priority tier, and a defined "done" state. Open-ended tasks are not acceptable.
- **No scope drift.** If a specialist identifies work that falls outside their brief, they stop and escalate to the PM — they do not expand scope on their own initiative.
- **Existing systems first.** Every brief must reference the specific existing code, infrastructure, or system the specialist is extending. The phrase "build from scratch" should not appear in any brief.

## Task Brief Template

---

**TASK BRIEF**

**Task Title:**
One line. Action verb + deliverable. Example: "Extend NURTURE agent to trigger email on lead score change."

**Objective:**
2–4 sentences. What this accomplishes and why it matters for the client blueprint.

**Existing Systems to Extend:**
List the specific files, endpoints, services, or infrastructure this work builds ON TOP OF.
- Example: `omerion/agents/crm_nurture/graph.py` — add new node, do not alter existing nodes
- Example: Supabase table `leads` — add new column via migration only (`IF NOT EXISTS`)
- Example: Python automation script `[Script Name]` — extend with new branch, do not modify existing branches

**Deliverable Format and Acceptance Criteria:**
- What is the output? (PR, migration file, automation script, documentation, etc.)
- What does "done" look like? List 3–5 specific, testable criteria.
- Example: "Email is triggered within 60 seconds of score change. Langfuse shows trace. pytest passes."

**Deadline:**
Specific date and time. No relative dates ("by Friday" → "by 2026-05-08 17:00 EST").

**Priority Tier:**
- **P0** — Production incident or blocking client go-live. Respond within 2 hours, resolve within 24.
- **P1** — Milestone-critical. Respond within 4 hours, resolve within 48.
- **P2** — Standard delivery. Respond within 24 hours, resolve per sprint plan.

**Communication Channel:**
Discord `#[project-channel]` for async updates. GitHub PR for code review. Google Meet for blockers that require live discussion.

**Check-in Cadence:**
- P0/P1: Update every 4 hours until resolved.
- P2: Daily async standup in Discord.

**Out of Scope:**
List explicitly what this brief does NOT include. Prevents scope creep.
- Example: "Do not modify existing HITL routing logic."
- Example: "Do not build a new dashboard — only extend the existing API endpoint."

---

# SECTION 4: Cost Reduction Without Quality Loss

## Build vs. Extend Decision Framework

Before any new tooling, service, or component is introduced into a client deployment, apply this decision sequence:

```
1. Does omerion.io already have infrastructure that covers this need?
   YES → Use it. Extend it. Do not build a parallel solution.
   NO  → Continue to step 2.

2. Can an existing omerion.io AI agent handle this task with a prompt or workflow change?
   YES → Modify the agent. Do not hire a human for this task.
   NO  → Continue to step 3.

3. Can an existing automation workflow or Python script handle this with a new branch?
   YES → Extend the workflow. Do not write new code.
   NO  → Continue to step 4.

4. Does an approved tool in the omerion.io stack have an API or integration for this?
   YES → Use the approved tool's API. Do not introduce a new vendor.
   NO  → Submit a new tooling request to Evykynn for review before proceeding.
```

**No new tool, framework, or vendor enters a client deployment without Evykynn's written approval.** The cost of integration debt and maintenance burden on unapproved tooling always exceeds the short-term convenience.

## Cost-Efficiency Scoring Model

| Role | Value / Dollar | Speed Score | Quality Ceiling | Preferred Use Case |
|---|---|---|---|---|
| AI Automation Engineer | High | High | Medium | Workflow orchestration, API glue |
| Backend Developer | High | Medium | High | New endpoints, migrations, integrations |
| Frontend Developer | Medium | Medium | High | Client dashboards, portal UI |
| Data & Research Specialist | High | Medium | Medium | Data pipelines, B2B enrichment |
| Prompt Engineer / AI Specialist | Very High | Low–Medium | Very High | Agent design, LLM optimization |
| CRM Integration Specialist | Medium | High | Medium | Client CRM sync and webhook routing |
| DevOps / Cloud Engineer | High | Medium | High | Deployment, CI/CD, infrastructure |
| QA & Testing Specialist | Very High | Medium | N/A | Pre-deploy validation, regression |
| Client Success Specialist | Medium | High | Medium | Post-deployment adoption |
| Project Manager | High | N/A | High | Delivery coordination |
| **omerion.io AI Agent** | **Very High** | **Very High** | **Bounded by design** | **Repeatable, rule-based, low-judgment tasks** |

**Speed Score** reflects time-to-output, not task complexity. **Quality Ceiling** reflects the maximum quality achievable in that role with the right hire.

## Freelancer vs. Retained Specialist vs. AI Agent

| Task Characteristic | Use |
|---|---|
| Repeatable, rule-based, high-volume | AI Agent (build within omerion.io) |
| One-time, bounded, deliverable-clear | Freelancer (project-based) |
| Ongoing, relationship-dependent, judgment-heavy | Retained Specialist |
| Client-facing, trust-critical | Retained Specialist or Direct Network |
| Experimental, uncertain scope | Freelancer (hourly, capped) |

**The default question before hiring any human for a task is: can an omerion.io agent do this?** If the answer is yes or probably, build or configure the agent first. Hiring a human for a task an agent can do is a margin leak.

---

# SECTION 5: Quality & Speed Standards

## What "Quality" Means at omerion.io

Quality is defined as output that is:
1. **Error-free** — no runtime errors, no broken integrations, no data inconsistencies
2. **Vision-aligned** — matches the approved blueprint, Evykynn's architecture, and the client's product intent
3. **Client-approved** — passes the client's UAT criteria, not just the internal QA checklist
4. **Documented** — every deliverable includes a brief written summary of what was built and how to maintain it

Quality is not "it works on my machine." Quality means it works in the target environment, under realistic load, with real client data.

## What "Speed" Means at omerion.io

Speed is defined by delivery SLAs, not by how fast a task is started.

| Priority Tier | Response Time | Resolution Time |
|---|---|---|
| P0 (production incident / go-live blocker) | 2 hours | 24 hours |
| P1 (milestone-critical) | 4 hours | 48 hours |
| P2 (standard sprint delivery) | 24 hours | Per sprint plan |

**Missing an SLA is an escalation event, not an acceptable outcome.** If a specialist cannot meet an SLA, they are required to notify the PM before the deadline — not after.

## Non-Negotiable Standards

Every team member operating in an omerion.io engagement must meet the following before any deliverable is accepted:

- **All code is submitted via GitHub PR. No direct pushes to main or production branches.**
- **All PRs must pass `python -m pytest omerion/ -x -q` (or equivalent) before QA review.**
- **All LangGraph nodes must use `@traced_node("node_name")` — untraceable nodes are rejected.**
- **All LLM calls must go through `ClaudeRouter` — direct `Anthropic()` instantiation is rejected.**
- **All SQL migrations must use `IF NOT EXISTS` — destructive migrations without explicit approval are rejected.**
- **No production environment variable or config file is modified without Evykynn's written approval.**
- **All task updates are written and async-first — no work happens without a documented trail.**
- **No scope changes are actioned without blueprint amendment and Evykynn's sign-off.**

## Escalation Protocol

When a contractor misses a quality or speed standard:

1. **First instance:** PM documents the miss, delivers written feedback, and sets a remediation window (24–48 hours for P1/P2 issues).
2. **Remediation window:** Contractor corrects the deliverable and resubmits for QA review.
3. **Second instance (same standard):** PM escalates to Evykynn. The contractor is placed on a performance watch. Future work is conditional on resolution.
4. **Third instance or P0 miss:** Engagement is terminated. The contractor loses access to all omerion.io systems immediately. The PM sourced a replacement.

**Speed misses and quality misses are treated equally.** A late but correct deliverable is a miss. An on-time but broken deliverable is a miss.

---

# SECTION 6: Talent Vetting SOP

## Step 1: Role-to-Blueprint Alignment Check

Before posting a role or reaching out to any candidate:
- Confirm the role is required by the approved blueprint — do not hire speculatively
- Define the exact deliverables this hire will own (reference Section 2 for scope)
- Confirm the compensation model and budget are approved

## Step 2: Portfolio Review Criteria

Review past work against the omerion.io stack specifically. Generic "I've done automation" is not sufficient.

| Role | Portfolio Evidence Required |
|---|---|
| AI Automation Engineer | Python automation scripts with multi-step error handling; Claude API integration samples |
| Backend Developer | FastAPI or equivalent async Python backend; Supabase or PostgreSQL migrations |
| Frontend Developer | React TypeScript projects; pixel-accurate implementation from design specs |
| Data & Research Specialist | Data pipeline samples with B2B enrichment sources (Apollo, Hunter, Clearbit, or similar) |
| Prompt Engineer | Claude or GPT prompt chains with documented output quality metrics; LangGraph samples preferred |
| CRM Integration Specialist | HubSpot, GoHighLevel, Pipedrive, or Salesforce API integration; bidirectional sync examples |
| DevOps Engineer | Docker Compose configurations; GitHub Actions CI/CD pipelines; VPS deployments |
| QA Specialist | pytest test suites; API testing collections; regression test documentation |
| Client Success | Onboarding documentation examples; client training recordings or materials |
| Project Manager | Sprint delivery artifacts; async team coordination evidence; AI or automation project history |

Reject candidates who cannot produce specific work samples from the stack areas listed above.

## Step 3: Technical Screening Questions

### AI Automation Engineer
1. Walk me through how you'd handle error recovery in a multi-step Python automation workflow where one step fails mid-execution.
2. How do you version-control automation scripts and agent configurations across environments?
3. Given a webhook payload from a Supabase database change, how would you route it to different processing branches based on the event type?
4. Describe a time you integrated two third-party APIs with conflicting data schemas. How did you handle the mapping?
5. How do you handle API rate limits in an automation workflow that runs on a schedule?

### Backend Developer
1. How do you write a Supabase migration that adds a column to a table already in production without downtime?
2. Walk me through how you'd implement a webhook receiver in FastAPI that validates HMAC signatures.
3. How do you structure async database calls in a FastAPI endpoint to avoid blocking the event loop?
4. What does Row Level Security mean in Supabase, and when would you use it?
5. How do you design an API endpoint that is consumed by a LangGraph agent node?

### Frontend Developer
1. How do you implement a realtime data subscription in React using the Supabase JS client?
2. Describe your approach to building a component that must exactly match a provided design spec — what's your process when the spec is ambiguous?
3. How do you manage TypeScript type safety across API responses that may return partial data?
4. What's your approach to state management in a React app that polls multiple data sources?

### Data & Research Specialist
1. Describe how you'd build a pipeline that ingests company data from Apollo.io, normalizes it, and upserts it into a PostgreSQL table on a daily schedule.
2. How do you handle schema drift when an enrichment API (Apollo, Clearbit, Hunter) changes its response format?
3. What's your approach to detecting and deduplicating contact records in a production database when the same person appears from multiple data sources?
4. How do you manage API rate limits across multiple enrichment providers running in parallel?

### Prompt Engineer / AI Systems Specialist
1. How do you select between Claude Opus, Sonnet, and Haiku for tasks in a multi-agent system?
2. Walk me through how you'd design a LangGraph node that requires a HITL approval step before continuing.
3. How do you evaluate and improve the consistency of LLM outputs across a prompt chain?
4. Describe a situation where a prompt that worked in testing failed in production. What caused it and how did you fix it?
5. How do you use Langfuse to identify which agent node is causing output degradation?

### CRM Integration Specialist
1. Describe how you'd implement a bidirectional sync between a Supabase table and HubSpot contacts without creating infinite webhook loops.
2. How do you handle API pagination when pulling large contact lists from GoHighLevel?
3. Walk me through authenticating with the Salesforce REST API using OAuth 2.0.
4. How do you map a source schema with 40 fields to a CRM with 15 fields? What's your decision process for handling unmapped fields?

### DevOps / Cloud Infrastructure Engineer
1. Walk me through how you'd containerize a FastAPI app with a Celery worker and Redis broker using Docker Compose.
2. How do you manage secrets in a GitHub Actions CI/CD pipeline that deploys to a Hostinger VPS?
3. Describe how you'd set up a zero-downtime deployment for a FastAPI service on a Linux VPS.
4. How do you configure nginx as a reverse proxy for a FastAPI application with multiple workers?
5. What monitoring would you put in place on a VPS hosting multiple agentic services?

### QA & Testing Specialist
1. How do you write a pytest integration test for a FastAPI endpoint that depends on a live Supabase database?
2. How do you test the output quality of an LLM-based agent when the output is qualitative, not deterministic?
3. Walk me through your regression testing process after a backend change that touches shared state.
4. How do you triage a defect that only appears intermittently in a production async system?

## Step 4: Trial Task Protocol

Every candidate who passes the portfolio and screening phase completes a paid, time-boxed trial task before engagement.

**Rules:**
- The trial task is real work from the active backlog — not a synthetic exercise
- It is compensated at the candidate's stated rate
- It has a defined time box (4–8 hours typical)
- It has explicit acceptance criteria the candidate receives upfront
- It is reviewed by QA and Evykynn before the engagement decision is made

The trial task is the most accurate signal available. A candidate who performs well in a screen but struggles on the trial task does not advance.

## Step 5: Vision Alignment Interview

Conducted by Evykynn or the PM. Purpose: confirm the candidate understands the "extend, don't rebuild" mandate.

**Key questions:**
1. "You're handed a working LangGraph agent. The client wants a new feature. Walk me through how you approach this without breaking existing functionality."
2. "You think there's a better tool for this job than what's in the current stack. What do you do?"
3. "The client asks you directly to make a change that isn't in the task brief. How do you respond?"
4. "You finish your task and realize there's adjacent code that could be improved. Do you improve it? Why or why not?"

**Passing signal:** The candidate defers to the approved architecture, escalates before diverging, and does not assume authority to make structural decisions.

**Failing signal:** The candidate describes rebuilding, substituting, or "improving" systems beyond their brief as a natural part of their process.

## Step 6: Contract and System Access

- **No system access is granted before a signed contract.**
- The contract must specify: scope, deliverables, rate, timeline, IP assignment, confidentiality, and termination conditions.
- Access is provisioned on a least-privilege basis: the specialist gets access only to the systems their brief requires, nothing more.
- Access credentials are managed by Evykynn — not self-provisioned by the contractor.

---

# SECTION 7: Post-Approval Workflow

## Day 0 — Blueprint Locked

- Blueprint is signed by the client
- Evykynn reviews the blueprint and identifies which specialist roles are required
- PM is assigned (or confirmed from existing team)
- Delivery plan framework is created from the blueprint milestones
- Internal project channel created in Discord
- Project folder created in Google Drive Knowledge Base (see Section 8)

## Days 1–2 — Task Briefs and Talent Engagement

- PM drafts Task Briefs for all Phase 1 deliverables (using the template in Section 3)
- Evykynn reviews and approves all Task Briefs before they are sent to contractors
- Required specialist roles are sourced and screened (or pulled from existing network)
- Contracts signed, rates confirmed
- Trial tasks issued to any new contractors (see Section 6, Step 4)

## Days 3–5 — Kickoff and Work Begins

- Kickoff call or async kickoff document issued to all engaged specialists
- System access provisioned on least-privilege basis
- All contractors confirm they have read and understood their Task Brief
- Work begins — Day 1 status update expected in Discord `#[project-channel]` by end of business

## Ongoing — Active Delivery

- **Daily async standup:** Each specialist posts a 3-line update in Discord: (1) what they completed, (2) what they're doing today, (3) any blockers.
- **Weekly milestone review:** PM produces a written milestone report for Evykynn — deliverables complete, in-progress, and at-risk.
- **Scope escalation:** Any request outside the approved blueprint is flagged to Evykynn within 24 hours. Work on the out-of-scope item does not begin until a blueprint amendment is signed.
- **PR cadence:** All code is submitted via GitHub PR. QA reviews before merge. No batching of multiple features into one PR.

## Final Phase — QA, UAT, and Sign-Off

- QA Specialist runs full test suite and regression against all blueprint deliverables
- All P0 and P1 defects are resolved before client UAT begins
- Client UAT period: client tests against their own success criteria (minimum 3 business days)
- Client provides written sign-off or a defect list — defects are triaged and resolved
- Evykynn reviews and approves the final production deployment

## Handoff

- Client Success Specialist conducts live onboarding session(s)
- Client-facing documentation is delivered (user guide, FAQ, escalation contacts)
- Support SLA period begins (as defined in blueprint — typically 30 days)
- All internal project documentation is finalized and stored in Google Drive (see Section 8)
- System access for all contractors is revoked within 24 hours of handoff completion
- PM produces a project close-out report for Evykynn: what was delivered, what took longer than planned, lessons for future engagements

---

# SECTION 8: Knowledge Base Integration

## Storage Platform

All SOP documents, role definitions, task briefs, delivery logs, and project artifacts are stored in the **omerion.io Google Drive Knowledge Base**.

## Folder Structure

```
omerion.io Google Drive/
├── _Agency Operations/
│   ├── SOPs/
│   │   ├── SOP-001 Agency Blueprint Overview.md
│   │   ├── SOP-002 Talent Identification Framework.md
│   │   ├── SOP-003 Delegation Protocols.md
│   │   └── [additional SOPs follow the same naming pattern]
│   ├── Role Templates/
│   │   ├── ROLE-AI-Automation-Engineer.md
│   │   ├── ROLE-Backend-Developer.md
│   │   └── [one file per role, updated as standards evolve]
│   └── Templates/
│       └── TASK-BRIEF-Template.md
│
├── _Client Engagements/
│   └── [CLIENT NAME] — [YYYY-MM]/
│       ├── Blueprint/
│       │   └── [CLIENT]-Blueprint-v1.0.md
│       ├── Task Briefs/
│       │   └── TB-001-[Task-Title].md
│       ├── Delivery Logs/
│       │   └── Milestone-[N]-Report.md
│       └── Handoff/
│           ├── User-Guide.md
│           └── Project-Closeout-Report.md
│
└── _Talent/
    └── [Contractor Name]/
        ├── Screening-Notes.md
        ├── Trial-Task-Assessment.md
        └── Contract-Reference.md (filename only — actual contract in secure storage)
```

## Naming Conventions

| Document Type | Convention | Example |
|---|---|---|
| SOP documents | `SOP-[NNN]-[Title-Kebab-Case].md` | `SOP-003-Delegation-Protocols.md` |
| Role definitions | `ROLE-[Role-Title-Kebab-Case].md` | `ROLE-Prompt-Engineer.md` |
| Task briefs | `TB-[NNN]-[Task-Title-Kebab-Case].md` | `TB-007-Extend-NURTURE-Email-Trigger.md` |
| Client blueprints | `[CLIENT]-Blueprint-v[N.N].md` | `AcmeGrowth-Blueprint-v1.2.md` |
| Milestone reports | `Milestone-[N]-Report-[YYYY-MM-DD].md` | `Milestone-3-Report-2026-06-15.md` |
| Closeout reports | `Project-Closeout-[CLIENT]-[YYYY-MM].md` | `Project-Closeout-AcmeGrowth-2026-07.md` |

## Internal vs. Client-Facing Classification

| Document | Classification | Shared With Client? |
|---|---|---|
| Blueprint | Client-facing | Yes — signed by client |
| Task briefs | Internal | No |
| Milestone reports | Internal (summary shared) | Summary version only |
| Role definitions | Internal | No |
| SOP documents | Internal | No |
| User guide / FAQ | Client-facing | Yes — delivered at handoff |
| Project closeout report | Internal | No |
| Contractor screening notes | Internal — restricted | No |
| Trial task assessments | Internal — restricted | No |

**Internal — restricted** documents are accessible only to Evykynn. Internal documents are accessible to the PM and relevant specialists on a need-to-know basis. Client-facing documents are delivered to the client in PDF or Google Doc format — never as raw source files with internal metadata.

---

*This document is the governing SOP for omerion.io talent operations. It is a living document — updated when processes change, roles evolve, or new standards are established. All updates require Evykynn's review and version increment before the updated version replaces the previous one.*
