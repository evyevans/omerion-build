---
description: Run a comprehensive WAT audit on a specific Omerion sub-agent. Reads skill.md, prompts.py, graph.py, state.py and evaluates execution readiness, backend alignment, and HITL coverage.
argument-hint: "Agent codename (SCOUT|SCORE|LEADS|NURTURE|REACH|MATCH|INTEL)"
---

You are auditing the Omerion sub-agent: **$ARGUMENTS**

Follow this exact sequence:

1. **Identify the agent module** — map the codename to its directory:
   - SCOUT → `omerion/agents/lead_scraper_enricher/`
   - SCORE → `omerion/agents/icp_scoring/`
   - LEADS → `omerion/agents/high_quality_lead_scraping/`
   - NURTURE → `omerion/agents/crm_nurture/`
   - REACH → `omerion/agents/linkedin_outreach/`
   - MATCH → `omerion/agents/offer_matching/`
   - INTEL → `omerion/agents/meeting_intelligence/`

2. **Read these files in order:**
   - `omerion/skills/<skill-name>.skill.md`
   - `<agent_dir>/prompts.py`
   - `<agent_dir>/graph.py`
   - `<agent_dir>/state.py`
   - `<agent_dir>/tools.py`

3. **Evaluate against WAT framework:**
   - **W** (W5H): Does the skill clearly define What/Why/Who/When/Where/How?
   - **A** (Agent Architecture): Does the graph match the skill contract?
   - **T** (Tool/API check): Are all declared tools implemented in tools.py?

4. **Check for these specific issues:**
   - HITL gating: Is `hitl: true` in skill.md implemented in graph.py?
   - Event types: Is `EventType` enum used (not raw strings)?
   - Checkpointer: Is `PostgresSaver` attached in `build()`?
   - Column naming: Is `contact_id` vs `id` consistent with other agents?
   - Langfuse: Is `@traced_node` on every node function?

5. **Output a WAT scorecard** (1-10) and flag any defects found.
