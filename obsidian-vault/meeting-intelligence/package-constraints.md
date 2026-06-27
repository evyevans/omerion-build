# Package Constraints

## Constraints by Package

These constraint blocks are injected into `synthesize_proposal` via the `constraint_fields` setting in agents.yaml. One block per service package. The agent must not generate proposal content that violates any constraint in the assigned package's block.

## revenue_acceleration_engine

- Do not promise specific revenue numbers — use ranges only ("20–35% improvement in speed-to-lead")
- Speed-to-lead SLA commitments require CRM read access to be confirmed before quoting a number
- AI-authored outreach (emails, DMs) requires a human G1 review gate before any live send — never promise fully autonomous outreach without mentioning the review step
- Automation scope is limited to top-of-funnel: prospecting, enrichment, first-touch outreach. Mid-funnel (proposals, negotiations, contracts) is out of scope for this package
- Do not reference competitor platforms (Outreach, Salesloft, Apollo) by name in the proposal

## ops_intelligence_layer

- Every automated decision node must have a documented human override mechanism — include this in the demo plan
- Reporting dashboards are read-only intelligence tools — no autonomous data writes, deletions, or modifications
- Integration with existing BI tools (Tableau, Power BI, Looker, Metabase) is in scope; migrating away from them is not
- Never frame the engagement as a headcount reduction play — always "leverage" and "multiply", never "replace" or "eliminate"
- Data pipelines must not touch financial statements or payroll data without explicit written sign-off (flag `compliance_concern` if these are mentioned)

## research_decision_stack

- All research outputs carry an implicit "AI-generated — verify before acting on" caveat; include this in success_metrics as a quality gate
- Real estate clients: every market claim must reference a data source (MLS, CoStar, public records, CMBS data); do not cite unsourced figures
- Competitive intelligence outputs are for internal decision-making only — not for direct inclusion in client-facing marketing materials
- Max latency SLA for research query turnaround: 24 hours; do not promise real-time or sub-hour results
- Never promise exclusive access to data sources — all sources cited must be available via public or licensed API

## process_automation_suite

- Scope cap: 3 distinct processes per engagement phase — do not propose automating more than 3 processes in any single phase, regardless of W5H scope signals
- Every automated process must have a documented rollback procedure in the blueprint before go-live
- Processes touching payroll, HR records, or financial statements require written legal sign-off before automation is built — flag `compliance_concern` and note this in `next_steps`
- A non-production test environment is mandatory before any automation goes live — include this as a Phase 1 milestone
- Do not propose replacing any SaaS tool the prospect currently pays for — frame as "layer on top of" not "replace"