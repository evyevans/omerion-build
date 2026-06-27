# Client Document Templates

## Doc Type: proposal

**Purpose:** Pre-sales document confirming scope and approach before contract is signed.
**Audience:** Prospect / economic buyer.
**Max length:** 600 words.

### Sections (in order — all required):
1. **Executive Summary** (≤150 words) — Pain identified in the discovery call, approach proposed, expected outcome. Lead with the problem. No boilerplate opener.
2. **Current State Assessment** — 3–5 bullet observations from the W5H. Every bullet must be a specific observation, not a generic category ("Your team spends 6 hours/week manually reconciling reports" not "Operational inefficiency").
3. **Recommended Approach** — Service package name, what it includes, what it explicitly excludes (scope boundary).
4. **Phased Delivery Plan** — Phase 1/2/3 milestones with week estimates. Format: "Phase 1 (Weeks 1–2): [milestone]."
5. **Investment** — Price band (e.g., "$5,200–$6,400/mo"), payment terms, what triggers each invoice.
6. **Next Steps** — Exactly 2 actions: (1) client sign-off action, (2) kickoff scheduling action with a specific date placeholder.

**Banned phrases:** "leverage", "synergies", "best-in-class", "unlock potential", "holistic", "cutting-edge", "game-changing".

---

## Doc Type: sow

**Purpose:** Binding scope agreement. More precise than proposal. Used after verbal agreement.
**Audience:** Signatory (legal/finance level).
**Max length:** 800 words.

### Sections (in order — all required):
1. **Parties** — "This Statement of Work is entered into between Omerion Inc. and [Client Legal Name] as of [Date]."
2. **Scope of Work** — Numbered list of deliverables. Each item ends with its acceptance criteria (quoted from the blueprint).
3. **Out of Scope** — 3–5 explicit exclusions that a client might reasonably assume are included.
4. **Timeline** — Start date, Phase 1/2/3 milestone dates, final delivery date. All dates are specific (no "approximately").
5. **Fees** — Line-item breakdown per phase. Payment schedule: "50% on signing, 50% on Phase 2 completion."
6. **Change Order Process** — "Any scope change requires a written change order signed by both parties before work begins. Change orders are priced at $[rate]/hour."
7. **Signatures** — "[Client Name] ___________  Date: ___" and "Omerion Inc. ___________  Date: ___"

---

## Doc Type: blueprint

**Purpose:** Technical handoff document for the build team. Internal-facing.
**Audience:** Builder agent and developer review.
**Max length:** 1,000 words + diagrams.

### Sections (in order — all required):
1. **Project Overview** — One paragraph: what we're building, why, for whom, which service package.
2. **Architecture Diagram** — Mermaid flowchart block showing main components and data flows. Minimum 5 nodes.
3. **Phase Breakdown** — Table: Phase | Task Slugs | Key Dependencies | Est. Days.
4. **Acceptance Criteria** — Bulleted list, one item per task slug, referencing the task's verifiable criteria.
5. **Data Schema Changes** — Tables touched, columns added, migrations required (migration file name).
6. **Integration Contracts** — For each external API: endpoint URL pattern, auth method, where test credentials live (e.g., "Railway env var: GITHUB_TOKEN").

---

## Doc Type: weekly_update

**Purpose:** Client-facing progress report. Sent every Friday.
**Audience:** Client primary contact.
**Max length:** 350 words.

### Sections (in order — all required):
1. **Week [N] Summary** — 2 sentences: what shipped, what is in flight.
2. **Completed This Week** — One bullet per merged PR: "[Task title] — [one-line outcome in plain English]."
3. **In Progress** — One bullet per open PR: "[Task title] — [current status, e.g., 'CI passing, awaiting review']."
4. **Blockers** — Items requiring client action (access, data, decisions). Write "None this week." if empty — do not omit the section.
5. **Next Week** — 2–3 bullets of planned completions.

**Tone:** Plain English. No jargon. Write as if explaining to someone who has never seen the codebase.

---

## Doc Type: handoff

**Purpose:** End-of-engagement knowledge transfer. Delivered at project close.
**Audience:** Client technical contact + anyone who will maintain the system.
**Max length:** 1,200 words.

### Sections (in order — all required):
1. **What Was Built** — Inventory table: Component | Description | Link (GitHub repo / Supabase table / Railway service).
2. **How to Run It** — Step-by-step numbered list: environment setup, deploy commands, cron schedules, Railway service restart procedure.
3. **How to Maintain It** — What breaks most often, how to fix it, monitoring dashboards to watch, who to contact.
4. **Known Limitations** — Honest bulleted list. Minimum 2 items. No system is perfect — this builds trust.
5. **Future Recommendations** — 3 highest-value next investments, ranked by ROI. One sentence each.
6. **Access & Credentials** — Where secrets live (e.g., "Railway environment variables — see service 'omerion-api'"). Never paste values. Link to 1Password vault or Railway dashboard.
