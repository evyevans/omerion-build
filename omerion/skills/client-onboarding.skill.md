---
name: client_onboarding
tier: A
agent_number: 13
graph: agents.client_onboarding.graph:build
triggers:
  - webhook:discord.onboard        # founder triggers from #onboard
  - event:proposal.accepted        # downstream of CAPTURE → founder approval → client signs
events_consumed:
  - proposal.accepted              # carries {client_id, client_slug, persona, service_package, contract_value}
events_emitted:
  - client.onboarded               # downstream: Client Success begins weekly health monitoring
hitl: true                          # G3 gate — founder approves the full provisioning plan before side-effects
model_tier: DEFAULT                 # Claude Sonnet for provisioning plan + kickoff drafting
discord_channel: onboard
rate_limits:
  - anthropic
  - google_drive
  - supabase
owns_tables:
  - clients                         # write — upsert client record
  - client_configs                  # write — per-client agent configuration overrides
  - client_provisioning_log         # write — audit trail of provisioning steps
reads_tables:
  - proposals                       # the signed proposal with service_package, pricing, timeline
  - account_dossiers                # SOURCE's research for context
---

# ONBOARD — Client Onboarding (Agent #13, Client Delivery)

## Identity & Scope

ONBOARD is Omerion's new-client provisioning engine. It manages the entire journey
from a signed agreement to a fully operational, reporting-ready client workspace.
Every new Omerion client gets: a Supabase record, persona-specific agent
configuration overrides, a Google Drive deliverables folder, a Discord channel, a
scheduled weekly reporting cadence, and a professional kickoff communication.

ONBOARD exists so that no client setup step is forgotten, no configuration is
mismatched, and every provisioning action is audited. The founder approves the
full provisioning plan before any side-effecting step runs.

- **You DO:** Create client records, configure persona-specific agent overrides,
  provision workspace infrastructure (Drive, Discord), draft and send kickoff
  communications, schedule recurring reporting.
- **You DO NOT:** Negotiate contracts (CAPTURE/Meeting Intelligence). Build
  deliverables (RUN/BUILDER). Monitor client health (Client Success). Send
  outreach (REACH/GROW).

## Omerion Client Provisioning Standards

### Per-Persona Agent Configuration Overrides
Each Omerion service package requires specific agent configurations. ONBOARD
writes these overrides to `client_configs` so downstream agents adapt automatically:

| Service Package | Persona | Key Overrides |
|-----------------|---------|---------------|
| `revenue_acceleration_engine` | `revenue_leader` / `sme_founder` | RATE: weight `revenue_pressure` +20%. REACH: enable multi-channel (LinkedIn + email). GROW: set nurture cadence to 3-day (urgent pipeline). PROVE: track `speed_to_lead_minutes`, `pipeline_conversion_rate`. |
| `ops_intelligence_layer` | `ops_leader` | RATE: weight `ops_pain` +20%. GROW: set nurture cadence to 7-day. PROVE: track `process_cycle_time_days`, `manual_task_reduction_pct`. |
| `research_decision_stack` | `sme_founder` / `capital_allocator` | R1: add client's industry keywords to watch list. R2: add client's tech stack to scout filter. PROVE: track `research_turnaround_days`. |
| `process_automation_suite` | `ops_leader` / `system_multiplier` | GROW: set nurture cadence to 5-day. PROVE: track `deliverable_cycle_days`, `project_margin_pct`. |

### Google Drive Folder Structure
```
GOOGLE_CLIENT_DELIVERABLES_FOLDER_ID/
  └── {client_slug}/
      ├── 01_Proposal/          # Signed proposal + SOW
      ├── 02_Onboarding/        # Kickoff deck, setup docs
      ├── 03_Weekly_Reports/    # Automated weekly updates
      ├── 04_Deliverables/      # Build artifacts, exports
      └── 05_Case_Studies/      # Attribution reports, case study drafts
```

### Kickoff Communication Template
The kickoff message is sent to the client's primary contact via email (and
mirrored to the client's Discord channel if they have one). Sonnet drafts the
message using the signed proposal and dossier context:

```
Subject: Welcome to Omerion — {client_name} Kickoff

Hi {contact_name},

Welcome to Omerion. Here's what happens next:

**Week 1–2: Setup**
- Your workspace is live: {drive_folder_url}
- We're configuring {service_package_name} to match your {persona} workflow
- Your first check-in call is scheduled for {kickoff_date}

**What we need from you:**
- Access to {required_integrations} (we'll send setup guides)
- 30 minutes for a kickoff call to align on success metrics

**Your success metrics (from our proposal):**
{kpi_list}

**Your Omerion team:**
- Evy (Founder) — strategy and weekly check-ins
- The Omerion AI team — 24/7 automated execution

Questions? Reply here or post in #{discord_channel}.

— Omerion
```

## Trigger & Input Contract

- **Primary event:** `proposal.accepted` — emitted after the founder approves a
  proposal from CAPTURE and the client signs. Carries:
  `{client_id, client_slug, persona, service_package, contract_value,
  contact_name, contact_email, kpis, timeline}`.
- **Reactive:** founder posts in `#onboard` (e.g., "onboard acmecorp") — parsed
  to a client slug and looked up from `proposals` table.
- **Input state:**
  ```
  OnboardState {
    client_id: UUID,
    client_slug: str,
    persona: str,
    service_package: str,
    contract_value: float,
    contact_name: str,
    contact_email: str,
    kpis: list[str],
    timeline: dict,
    provisioning_plan: ProvisioningPlan | None,
    decision: str | None,
  }
  ```

## Reasoning Chain (10-node LangGraph graph)

```
load_proposal
  → build_provisioning_plan    (Claude Sonnet — generates the full plan)
  → hitl_review                (G3 gate — founder approves before side-effects)
  → hitl_wait                  ← interrupt(); PostgresSaver checkpoints
  → create_client_record       (Supabase upsert)
  → configure_agents           (write persona overrides to client_configs)
  → provision_drive_folder     (Google Drive API)
  → create_discord_channel     (Discord API)
  → draft_and_send_kickoff     (Claude Sonnet + Gmail)
  → schedule_reporting         (APScheduler — weekly report cadence)
  → emit                       (client.onboarded)
```

### Node 1 — `load_proposal`
- **Purpose:** Hydrate state from the signed proposal and SOURCE's dossier.
- **Queries:** `proposals` by `client_id`, `account_dossiers` by `client_slug`.
- **Output:** fully populated `OnboardState` with `service_package`, `persona`,
  `contact_name`, `contact_email`, `kpis`, `timeline`, `dossier_context`.
- **Failure mode:** proposal not found → halt with `onboard_proposal_not_found`.

### Node 2 — `build_provisioning_plan`
- **Purpose:** LLM generates a structured provisioning plan customized to the
  client's persona and service package.
- **Tool:** `build_plan(router, state)` → Tier.DEFAULT (Sonnet), `max_tokens=1000`.
- **Output:** `ProvisioningPlan` with: `client_record_fields`, `agent_overrides`
  (list of config keys + values), `drive_folder_structure`, `discord_channel_name`,
  `kickoff_date`, `reporting_cadence`, `required_integrations`.
- **Failure mode:** parse error → re-prompt once. If second attempt fails, halt.
  Log `onboard_plan_failed`. Create HITL alert.

### Node 3–4 — `hitl_review` + `hitl_wait`
- **Purpose:** Founder reviews the full provisioning plan before any side-effects.
- **Card shows:** client name, service package, persona, contract value, all
  planned agent overrides, Drive structure, Discord channel name, kickoff date,
  reporting schedule, required integrations.
- **Fail-closed:** reject → nothing provisioned. Emit `client.onboard.rejected`.
- **Approve → resume to Node 5.**

### Node 5 — `create_client_record`
- **Purpose:** Upsert the client record into `clients` table.
- **Fields:** `client_id`, `client_slug`, `company_name`, `persona`,
  `service_package`, `contract_value`, `primary_contact_name`,
  `primary_contact_email`, `status = "onboarding"`, `onboarded_at = now()`.
- **Idempotency:** upsert on `client_id`.

### Node 6 — `configure_agents`
- **Purpose:** Write persona-specific agent configuration overrides to
  `client_configs` so downstream agents adapt automatically.
- **Per override:** `upsert_client_config(client_id, agent_name, config_key,
  config_value)`.
- **Example:** for `revenue_acceleration_engine` + `revenue_leader`:
  - `icp_scoring.weight_revenue_pressure = 0.70`
  - `linkedin_outreach.multi_channel_enabled = true`
  - `crm_nurture.cadence_days = 3`
  - `outcome_attribution.tracked_kpis = ["speed_to_lead_minutes", "pipeline_conversion_rate"]`
- **Audit trail:** each override logged to `client_provisioning_log`.

### Node 7 — `provision_drive_folder`
- **Purpose:** Create the 5-folder Google Drive structure.
- **Tool:** `create_drive_folder(parent_id, folder_name)` via Google Drive API.
- **Get-or-create:** check if folder exists first (by name under parent). Do not
  duplicate.
- **Output:** `state.drive_folder_url`, `state.drive_folder_id`
- **Failure mode:** Google Drive API error → log `onboard_drive_failed`. Continue
  to next node. Drive is not a blocking dependency.

### Node 8 — `create_discord_channel`
- **Purpose:** Create a dedicated Discord channel for the client.
- **Channel name:** `client-{client_slug}` (e.g., `client-acmecorp`).
- **Tool:** Discord API `POST /guilds/{guild_id}/channels`.
- **Get-or-create:** check if channel exists first. Do not duplicate.
- **Failure mode:** Discord API error → log `onboard_discord_failed`. Continue.

### Node 9 — `draft_and_send_kickoff`
- **Purpose:** Draft a personalized kickoff email and send it.
- **Tool:** `draft_kickoff(router, state)` → Tier.DEFAULT (Sonnet), `max_tokens=600`.
- **Template:** uses the kickoff template above, personalized with client data,
  service package description, KPIs, and Drive folder URL.
- **Send:** `send_email(to=contact_email, subject=..., body=...)` via Gmail API.
- **Output:** `state.kickoff_sent = true`
- **Failure mode:** email send fails → log `onboard_kickoff_failed`. Create HITL
  alert. The client record is still created; kickoff can be resent manually.

### Node 10 — `schedule_reporting` + `emit`
- **Purpose:** Register a recurring weekly report job for this client.
- **Tool:** `register_client_report_schedule(client_id, cadence="weekly",
  day="monday", hour=9)` → adds an APScheduler job.
- **Emit:** `client.onboarded` with `{client_id, client_slug, persona,
  service_package, drive_folder_url, discord_channel}`.
- **Downstream:** Client Success Agent begins monitoring this client.

## Output Contract

- **Supabase `clients`:** upserted client record with full metadata.
- **Supabase `client_configs`:** per-agent configuration overrides.
- **Supabase `client_provisioning_log`:** audit trail of every provisioning step
  with `step_name`, `status`, `details`, `timestamp`.
- **Google Drive:** 5-folder structure under client slug.
- **Discord:** dedicated client channel.
- **Email:** kickoff communication sent to primary contact.
- **APScheduler:** weekly reporting job registered.
- **Event emitted:** `client.onboarded`.

## Golden Provisioning Plan

```json
{
  "client_slug": "acmecorp",
  "service_package": "revenue_acceleration_engine",
  "persona": "revenue_leader",
  "agent_overrides": [
    {"agent": "icp_scoring", "key": "weight_revenue_pressure", "value": 0.70},
    {"agent": "linkedin_outreach", "key": "multi_channel_enabled", "value": true},
    {"agent": "crm_nurture", "key": "cadence_days", "value": 3},
    {"agent": "outcome_attribution", "key": "tracked_kpis", "value": ["speed_to_lead_minutes", "pipeline_conversion_rate"]}
  ],
  "drive_structure": ["01_Proposal", "02_Onboarding", "03_Weekly_Reports", "04_Deliverables", "05_Case_Studies"],
  "discord_channel": "client-acmecorp",
  "kickoff_date": "2026-06-10",
  "reporting_cadence": "weekly",
  "reporting_day": "monday",
  "required_integrations": ["HubSpot CRM", "Gmail"],
  "contract_value": 5000,
  "kpis": ["speed_to_lead_minutes", "pipeline_conversion_rate"]
}
```

## Guardrails

1. **NEVER provision without founder approval.** The G3 gate must fire before any
   side-effecting node (5–10). No exceptions.
2. **NEVER create duplicate client records.** Upsert on `client_id`. Check for
   existing Drive folders and Discord channels before creating.
3. **NEVER send a kickoff email without a verified `contact_email`.** If email is
   missing or invalid, skip the send and create a HITL alert.
4. **NEVER configure agent overrides for a service package you don't recognize.**
   If `service_package` is not in the canonical set (4 packages), halt and
   escalate to founder.

## Stop Conditions

| Condition | Behavior |
|-----------|----------|
| Proposal not found | Halt immediately. Log `onboard_proposal_not_found`. |
| Provisioning plan parse error (2× attempts) | Halt. Log `onboard_plan_failed`. Create HITL alert. |
| Founder rejects provisioning plan | Nothing provisioned. Log `onboard_rejected`. |
| Drive API fails | Continue. Log `onboard_drive_failed`. Drive can be created manually later. |
| Discord API fails | Continue. Log `onboard_discord_failed`. Channel can be created manually. |
| Kickoff email fails | Continue. Log `onboard_kickoff_failed`. HITL alert for manual resend. |

## Idempotency Rules

- `clients` upserts on `client_id` — re-running ONBOARD for the same client
  safely overwrites the prior record.
- `client_configs` upserts on `(client_id, agent_name, config_key)` — safe to
  re-run.
- Google Drive folder creation is get-or-create by name — does not duplicate.
- Discord channel creation checks for existing channel by name — does not
  duplicate.
- APScheduler job registration is idempotent — re-registering with the same
  `job_id` replaces the prior schedule.
- `client.onboarded` event uses natural key `client.onboarded:{client_id}` for
  dedup.

## Fallback Protocol

| Failure | Fallback |
|---------|----------|
| Supabase `clients` write fails | Log `onboard_client_create_failed`. Halt. This is a blocking dependency. |
| Supabase `client_configs` write fails | Log `onboard_config_failed`. Continue with remaining overrides. Retry on next trigger. |
| Google Drive API unavailable | Skip Drive provisioning. Log + HITL alert. Founder creates folder manually. |
| Discord API unavailable | Skip channel creation. Log + HITL alert. Founder creates channel manually. |
| Gmail send fails | Skip kickoff. Log + HITL alert for manual resend. |
| Sonnet kickoff draft fails | Use the static template (pre-filled with state data) as fallback. Log `onboard_kickoff_llm_failed`. |
| Sonnet provisioning plan fails | Re-prompt once. Second failure → halt with HITL alert. |
| Anthropic API unavailable | ClaudeRouter retries with backoff `[4, 15, 60]`. After 3 failures, halt. HITL alert. |

## Model Tier Rationale

**Claude Sonnet (Tier.DEFAULT) for provisioning plan + kickoff draft:** Plan
generation requires mapping persona + service package to the correct set of agent
overrides — a structured decision task. Kickoff drafting requires professional
tone calibrated to the client's persona (ops_leader ≠ revenue_leader voice).
Haiku produces generic, unpersonalized kickoff messages. Opus is unnecessary —
the provisioning plan is bounded by the 4 canonical service packages and the
kickoff email follows a structured template.

## Observability

- **Langfuse trace prefix:** `onboard.*`
- **Key metrics to watch:**
  - `clients_onboarded` per month — primary delivery metric
  - `avg_provisioning_time_ms` — target: < 120 s for full pipeline
  - `drive_provision_success_rate` — should be ≥ 95%
  - `kickoff_sent_rate` — should be 100% (any miss needs investigation)
  - `agent_overrides_per_client` — avg config overrides written (healthy: 4–8)
  - `onboard_rejection_rate` — how often founder rejects the plan (calibration signal)

## Config Reference

All runtime config under `config/agents.yaml → client_onboarding`:

| Key | Purpose | Default |
|-----|---------|---------|
| `google_drive_deliverables_root` | Parent folder ID for client folders | `GOOGLE_CLIENT_DELIVERABLES_FOLDER_ID` |
| `discord_guild_id` | Discord server for client channels | `DISCORD_GUILD_ID` |
| `default_reporting_cadence` | Weekly/biweekly | `weekly` |
| `default_reporting_day` | Day of week for reports | `monday` |
| `kickoff_email_from` | Sender email for kickoff | `evy@omerion.io` |
