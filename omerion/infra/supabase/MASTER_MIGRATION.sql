-- ============================================================
-- OMERION MASTER MIGRATION — safe to re-run (IF NOT EXISTS)
-- Run this ENTIRE script in Supabase SQL Editor
-- Generated automatically — do not edit manually
-- ============================================================

-- ─── 0001_extensions_and_enums.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0001 — Extensions & Enums
-- Foundation types & extensions for Omerion internal agent OS.
-- ════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "postgis";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ─── Personas (client-facing segmentation) ─────────────────────────
DO $$ BEGIN
  CREATE TYPE persona AS ENUM ('team_lead',
    'investor',
    'property_manager',
    'wholesaler',
    'brokerage',
    'solo_agent');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── Account lifecycle ─────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE account_status AS ENUM ('new',
    'existing',
    'changed',
    'disqualified',
    'client',
    'churned');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE account_tier AS ENUM ('tier_1', 'tier_2', 'tier_3');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── Contact / opportunity stages ──────────────────────────────────
DO $$ BEGIN
  CREATE TYPE contact_status AS ENUM ('new',
    'enriched',
    'scored',
    'engaged',
    'qualified',
    'do_not_contact',
    'opted_out');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE opportunity_stage AS ENUM ('new_lead',
    'contacted',
    'engaged',
    'discovery_booked',
    'discovery_done',
    'proposal_sent',
    'won',
    'lost',
    'paused');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── Score segments ────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE score_segment AS ENUM ('hot', 'warm', 'watchlist', 'cold');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── Build pipeline ────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE build_task_status AS ENUM ('pending',
    'in_progress',
    'review',
    'merged',
    'blocked',
    'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE deployment_status AS ENUM ('queued',
    'deploying',
    'live',
    'rolled_back',
    'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── HITL review ───────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE hitl_decision AS ENUM ('pending',
    'approved',
    'rejected',
    'expired',
    'escalated');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── Outreach channels ─────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE outreach_channel AS ENUM ('email', 'sms', 'linkedin_dm', 'linkedin_connection', 'voice');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE outreach_direction AS ENUM ('outbound', 'inbound');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── R&D lifecycle ─────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE rd_proposal_status AS ENUM ('draft',
    'submitted',
    'approved',
    'rejected',
    'in_build',
    'shipped',
    'retired');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- ─── 0002_core_tables.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0002 — Core tables: markets, accounts, contacts, events
-- Data spine shared by all 14 agents.
-- ════════════════════════════════════════════════════════════════════

-- ─── markets (owned by: Market Mapper #1) ──────────────────────────
CREATE TABLE IF NOT EXISTS markets (
    market_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name             TEXT NOT NULL UNIQUE,
    geo              geography(Polygon, 4326),
    bounding_box     geometry(Polygon, 4326),
    tier_label       account_tier DEFAULT 'tier_1',
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_markets_geo ON markets USING GIST (geo);

-- ─── accounts (owned by: Market Mapper #1) ─────────────────────────
CREATE TABLE IF NOT EXISTS accounts (
    account_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                  TEXT NOT NULL,
    domain                TEXT,
    website               TEXT,
    linkedin_company_url  TEXT,
    persona               persona NOT NULL,
    tier                  account_tier DEFAULT 'tier_1',
    status                account_status NOT NULL DEFAULT 'new',
    market_id             UUID REFERENCES markets(market_id) ON DELETE SET NULL,
    volume_bucket         TEXT,
    team_size_bucket      TEXT,
    tech_maturity_signals JSONB DEFAULT '[]'::jsonb,
    score                 NUMERIC(5,4) DEFAULT 0,
    confidence            NUMERIC(5,4) DEFAULT 0,
    first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_refreshed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata              JSONB DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- idempotency for Market Mapper re-runs
    UNIQUE (domain, market_id)
);
CREATE INDEX IF NOT EXISTS idx_accounts_persona_tier ON accounts (persona, tier);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts (status);
CREATE INDEX IF NOT EXISTS idx_accounts_score ON accounts (score DESC);
CREATE INDEX IF NOT EXISTS idx_accounts_domain_trgm ON accounts USING GIN (domain gin_trgm_ops);

-- ─── contacts (owned by: Lead Scraper & Enricher #3) ───────────────
CREATE TABLE IF NOT EXISTS contacts (
    contact_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id            UUID REFERENCES accounts(account_id) ON DELETE CASCADE,
    first_name            TEXT,
    last_name             TEXT,
    email                 TEXT,
    phone                 TEXT,
    linkedin_url          TEXT,
    role                  TEXT,
    persona               persona NOT NULL,
    problem_hypothesis    TEXT,
    status                contact_status NOT NULL DEFAULT 'new',
    do_not_contact        BOOLEAN NOT NULL DEFAULT false,
    opt_out_email         BOOLEAN NOT NULL DEFAULT false,
    opt_out_sms           BOOLEAN NOT NULL DEFAULT false,
    opt_out_linkedin      BOOLEAN NOT NULL DEFAULT false,
    enrichment_confidence JSONB DEFAULT '{}'::jsonb,
    founder_priority      BOOLEAN NOT NULL DEFAULT false,
    tags                  TEXT[] DEFAULT '{}',
    last_touch_at         TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- dedup key: email is primary, fall back to linkedin
    UNIQUE (email),
    UNIQUE (linkedin_url)
);
CREATE INDEX IF NOT EXISTS idx_contacts_account ON contacts (account_id);
CREATE INDEX IF NOT EXISTS idx_contacts_persona_status ON contacts (persona, status);
CREATE INDEX IF NOT EXISTS idx_contacts_priority ON contacts (founder_priority) WHERE founder_priority = true;

-- ─── events (universal event bus — owned by: all agents) ───────────
-- Canonical event stream from blueprint §9.2.
CREATE TABLE IF NOT EXISTS events (
    event_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    type         TEXT NOT NULL,          -- e.g. 'contact.enriched', 'blueprint.approved'
    source_agent TEXT NOT NULL,          -- e.g. 'lead_scraper_enricher'
    contact_id   UUID REFERENCES contacts(contact_id) ON DELETE SET NULL,
    account_id   UUID REFERENCES accounts(account_id) ON DELETE SET NULL,
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    correlation_id UUID,                  -- links related events across agents
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events (type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_contact ON events (contact_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_account ON events (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_correlation ON events (correlation_id);

-- Realtime replication for event-driven agent triggers
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication pub
    JOIN pg_publication_rel pr ON pr.prpubid = pub.oid
    JOIN pg_class cls ON cls.oid = pr.prrelid
    JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
    WHERE pub.pubname = 'supabase_realtime' AND nsp.nspname = 'public' AND cls.relname = 'events'
  ) THEN ALTER PUBLICATION supabase_realtime ADD TABLE public.events; END IF;
END $$;


-- ─── 0003_pipeline_tables.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0003 — Pipeline: scores, opportunities, blueprints, build, measurement
-- ════════════════════════════════════════════════════════════════════

-- ─── scores (owned by: ICP Scoring #6) ─────────────────────────────
CREATE TABLE IF NOT EXISTS scores (
    score_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id    UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    fit_score     NUMERIC(5,4) NOT NULL,
    intent_score  NUMERIC(5,4) NOT NULL,
    timing_score  NUMERIC(5,4) NOT NULL,
    final_score   NUMERIC(5,4) NOT NULL,
    segment       score_segment NOT NULL,
    rationale     TEXT,
    recommended_action TEXT,
    run_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    weights_snapshot JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- latest-per-contact-per-day
    UNIQUE (contact_id, run_date)
);
CREATE INDEX IF NOT EXISTS idx_scores_final_run ON scores (run_date DESC, final_score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_segment ON scores (segment, run_date DESC);

-- ─── opportunities (owned by: ICP Scoring #6 / Offer Matching #7) ──
CREATE TABLE IF NOT EXISTS opportunities (
    opportunity_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id       UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    account_id       UUID REFERENCES accounts(account_id) ON DELETE SET NULL,
    stage            opportunity_stage NOT NULL DEFAULT 'new_lead',
    offer_memo_url   TEXT,
    offer_modules    TEXT[] DEFAULT '{}',    -- e.g. ['DAAM','ORIA']
    offer_tier       TEXT,                   -- 'starter' | 'growth' | 'enterprise'
    value_est_usd    NUMERIC(12,2),
    pricing_band     JSONB,
    open_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    close_date       DATE,
    won_amount_usd   NUMERIC(12,2),
    lost_reason      TEXT,
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_opportunities_stage ON opportunities (stage);
CREATE INDEX IF NOT EXISTS idx_opportunities_contact ON opportunities (contact_id);

-- ─── blueprints (owned by: Meeting Intelligence #8) ────────────────
CREATE TABLE IF NOT EXISTS blueprints (
    blueprint_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    opportunity_id   UUID REFERENCES opportunities(opportunity_id) ON DELETE CASCADE,
    meeting_id       TEXT,       -- external Fireflies meeting_id
    transcript_url   TEXT,
    w5h              JSONB NOT NULL,  -- {who, what, where, when, why, how}
    constraints      JSONB DEFAULT '{}'::jsonb,
    architecture_md  TEXT,        -- markdown blueprint doc
    blueprint_url    TEXT,        -- Drive/Notion link
    backlog          JSONB NOT NULL DEFAULT '[]'::jsonb, -- [{task_name, module, priority, ...}]
    hitl_flags       JSONB DEFAULT '[]'::jsonb,
    status           TEXT NOT NULL DEFAULT 'draft',  -- draft | approved | rejected | shipped
    founder_approved_at TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_blueprints_opp ON blueprints (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_blueprints_status ON blueprints (status);

-- ─── build_tasks (owned by: Build Orchestrator #9) ─────────────────
CREATE TABLE IF NOT EXISTS build_tasks (
    task_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    blueprint_id    UUID NOT NULL REFERENCES blueprints(blueprint_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    module          TEXT NOT NULL,   -- DAAM | ORIA | RORA | ASAP | internal
    spec_md         TEXT NOT NULL,
    spec_url        TEXT,
    acceptance_criteria JSONB DEFAULT '[]'::jsonb,
    branch          TEXT,
    pr_url          TEXT,
    github_issue_number INT,
    ci_status       TEXT,
    status          build_task_status NOT NULL DEFAULT 'pending',
    priority        INT DEFAULT 100,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_build_tasks_blueprint ON build_tasks (blueprint_id);
CREATE INDEX IF NOT EXISTS idx_build_tasks_status ON build_tasks (status, priority);

-- ─── deployments (owned by: Build Orchestrator #9) ─────────────────
CREATE TABLE IF NOT EXISTS deployments (
    deployment_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    blueprint_id    UUID REFERENCES blueprints(blueprint_id) ON DELETE SET NULL,
    opportunity_id  UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    client_id       UUID,   -- future clients table
    modules_deployed TEXT[] NOT NULL,
    go_live_date    TIMESTAMPTZ NOT NULL,
    status          deployment_status NOT NULL DEFAULT 'queued',
    rollback_to     UUID REFERENCES deployments(deployment_id),
    release_notes   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments (status);
CREATE INDEX IF NOT EXISTS idx_deployments_go_live ON deployments (go_live_date DESC);

-- ─── revenue_events (owned by: Outcome Attribution #10) ────────────
CREATE TABLE IF NOT EXISTS revenue_events (
    revenue_event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id        UUID,
    opportunity_id   UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    event_type       TEXT NOT NULL,   -- closed_won | expansion | churn | renewal
    amount_usd       NUMERIC(12,2) NOT NULL,
    occurred_at      TIMESTAMPTZ NOT NULL,
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_revenue_events_client ON revenue_events (client_id, occurred_at DESC);

-- ─── lead_conversions (owned by: Outcome Attribution #10) ──────────
CREATE TABLE IF NOT EXISTS lead_conversions (
    conversion_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id       UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    opportunity_id   UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    from_stage       opportunity_stage,
    to_stage         opportunity_stage,
    converted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lead_conversions_contact ON lead_conversions (contact_id, converted_at DESC);

-- ─── agent_actions (audit trail — owned by: all agents) ────────────
CREATE TABLE IF NOT EXISTS agent_actions (
    action_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name     TEXT NOT NULL,
    action_type    TEXT NOT NULL,
    target_type    TEXT,       -- 'contact' | 'account' | 'opportunity' | etc.
    target_id      UUID,
    client_id      UUID,
    outcome_flag   TEXT,       -- 'success' | 'failure' | 'skipped'
    cost_usd       NUMERIC(8,4),
    payload        JSONB DEFAULT '{}'::jsonb,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_actions_agent_time ON agent_actions (agent_name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_actions_target ON agent_actions (target_type, target_id);

-- ─── attribution_reports (owned by: Outcome Attribution #10) ───────
CREATE TABLE IF NOT EXISTS attribution_reports (
    report_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deployment_id    UUID REFERENCES deployments(deployment_id) ON DELETE CASCADE,
    client_id        UUID,
    kpi_deltas       JSONB NOT NULL,
    summary          TEXT,
    proof_point      TEXT,
    attribution_model TEXT NOT NULL,
    window_days      INT NOT NULL,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_attribution_reports_deployment ON attribution_reports (deployment_id);


-- ─── 0004_operational_tables.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0004 — Operational tables: outreach, research, HITL queue
-- ════════════════════════════════════════════════════════════════════

-- ─── outbound_communications (owned by: LinkedIn #4 / CRM Nurture #5) ─
CREATE TABLE IF NOT EXISTS outbound_communications (
    comm_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id     UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    channel        outreach_channel NOT NULL,
    direction      outreach_direction NOT NULL DEFAULT 'outbound',
    sequence_id    UUID,
    sequence_step  INT,
    template_key   TEXT,
    subject        TEXT,
    body           TEXT NOT NULL,
    sent_at        TIMESTAMPTZ,
    delivered_at   TIMESTAMPTZ,
    opened_at      TIMESTAMPTZ,
    clicked_at     TIMESTAMPTZ,
    replied_at     TIMESTAMPTZ,
    bounced_at     TIMESTAMPTZ,
    provider_id    TEXT,   -- Twilio SID, Gmail Message-ID, LinkedIn activity ID
    status         TEXT NOT NULL DEFAULT 'queued',
    idempotency_key UUID NOT NULL UNIQUE,
    error_detail   JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_comms_contact ON outbound_communications (contact_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_comms_channel_status ON outbound_communications (channel, status);

-- ─── nurture_sequences (owned by: CRM Nurture #5) ──────────────────
CREATE TABLE IF NOT EXISTS nurture_sequences (
    sequence_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id     UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    stage          opportunity_stage NOT NULL,
    persona        persona NOT NULL,
    template_chain TEXT[] NOT NULL,
    current_step   INT NOT NULL DEFAULT 0,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_touch_at  TIMESTAMPTZ,
    paused_reason  TEXT,
    status         TEXT NOT NULL DEFAULT 'active', -- active | paused | completed | stopped
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (contact_id, stage)
);
CREATE INDEX IF NOT EXISTS idx_nurture_sequences_status ON nurture_sequences (status);

-- ─── contact_activity_log (owned by: CRM Nurture #5 / LinkedIn #4) ──
CREATE TABLE IF NOT EXISTS contact_activity_log (
    activity_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id     UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    activity_type  TEXT NOT NULL,   -- email_open | link_click | sms_delivered | linkedin_view | ...
    channel        outreach_channel,
    comm_id        UUID REFERENCES outbound_communications(comm_id) ON DELETE SET NULL,
    tracking_id    TEXT,
    metadata       JSONB DEFAULT '{}'::jsonb,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activity_contact_time ON contact_activity_log (contact_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_type_time ON contact_activity_log (activity_type, occurred_at DESC);

-- ─── research_dossiers (owned by: High-Quality Lead Scraping #2) ───
CREATE TABLE IF NOT EXISTS research_dossiers (
    dossier_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id     UUID REFERENCES contacts(contact_id) ON DELETE CASCADE,
    account_id     UUID REFERENCES accounts(account_id) ON DELETE CASCADE,
    summary        TEXT NOT NULL,
    source_urls    JSONB NOT NULL DEFAULT '[]'::jsonb,
    pain_signals   JSONB NOT NULL DEFAULT '[]'::jsonb,
    outreach_angles JSONB NOT NULL DEFAULT '[]'::jsonb,
    conversation_hooks JSONB DEFAULT '[]'::jsonb,
    offer_match    JSONB,   -- suggested DAAM/ORIA/RORA/ASAP combo
    confidence_score NUMERIC(5,4) DEFAULT 0,
    founder_approved BOOLEAN DEFAULT false,
    pinecone_ids   TEXT[] DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dossiers_account ON research_dossiers (account_id);
CREATE INDEX IF NOT EXISTS idx_dossiers_contact ON research_dossiers (contact_id);

-- ─── generated_drafts (owned by: any agent that drafts copy) ───────
-- Corpus of every AI-generated draft (approved or not). Feeds R&D.
CREATE TABLE IF NOT EXISTS generated_drafts (
    draft_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name     TEXT NOT NULL,
    contact_id     UUID REFERENCES contacts(contact_id) ON DELETE SET NULL,
    opportunity_id UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    purpose        TEXT NOT NULL,   -- 'outreach_email' | 'sms' | 'blueprint' | 'offer_memo' | ...
    model          TEXT NOT NULL,
    prompt_hash    TEXT,
    draft_body     TEXT NOT NULL,
    draft_metadata JSONB DEFAULT '{}'::jsonb,
    approved       BOOLEAN,
    founder_feedback TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_drafts_agent ON generated_drafts (agent_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_drafts_purpose_approved ON generated_drafts (purpose, approved);

-- ─── founder_review_queue (owned by: all agents via omerion_core.hitl) ─
CREATE TABLE IF NOT EXISTS founder_review_queue (
    review_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name       TEXT NOT NULL,
    session_id       TEXT NOT NULL,    -- OpenClaw session ID
    correlation_id   UUID NOT NULL,
    subject          TEXT NOT NULL,
    context_md       TEXT NOT NULL,    -- all relevant context rendered as markdown
    draft_ref        JSONB NOT NULL,   -- {type, id, url, body_excerpt}
    approve_token    TEXT NOT NULL UNIQUE,  -- cryptographic, one-time
    reject_token     TEXT NOT NULL UNIQUE,
    decision         hitl_decision NOT NULL DEFAULT 'pending',
    decision_notes   TEXT,
    delegated_to     TEXT,
    escalated_at     TIMESTAMPTZ,
    decided_at       TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '48 hours')
);
CREATE INDEX IF NOT EXISTS idx_review_queue_decision ON founder_review_queue (decision, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_review_queue_session ON founder_review_queue (session_id);
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication pub
    JOIN pg_publication_rel pr ON pr.prpubid = pub.oid
    JOIN pg_class cls ON cls.oid = pr.prrelid
    JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
    WHERE pub.pubname = 'supabase_realtime' AND nsp.nspname = 'public' AND cls.relname = 'founder_review_queue'
  ) THEN ALTER PUBLICATION supabase_realtime ADD TABLE public.founder_review_queue; END IF;
END $$;


-- ─── 0005_telemetry_and_rd.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0005 — Telemetry & R&D: agent observability + recursive improvement
-- ════════════════════════════════════════════════════════════════════

-- ─── agent_telemetry (owned by: all agents via omerion_core.telemetry) ─
CREATE TABLE IF NOT EXISTS agent_telemetry (
    telemetry_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name       TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    run_id           UUID NOT NULL,
    node_name        TEXT,
    started_at       TIMESTAMPTZ NOT NULL,
    ended_at         TIMESTAMPTZ,
    duration_ms      INT,
    status           TEXT NOT NULL,   -- 'success' | 'failure' | 'hitl_pending' | 'retrying'
    tokens_input     INT DEFAULT 0,
    tokens_output    INT DEFAULT 0,
    cost_usd         NUMERIC(10,6) DEFAULT 0,
    model_used       TEXT,
    hitl_wait_ms     INT DEFAULT 0,
    error            JSONB,
    metadata         JSONB DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_telemetry_agent_time ON agent_telemetry (agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_session ON agent_telemetry (session_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_run ON agent_telemetry (run_id);

-- ─── agent_performance_metrics (owned by: R4) ──────────────────────
-- R4 rolls agent_telemetry into pre-aggregated KPI rows for fast reads
CREATE TABLE IF NOT EXISTS agent_performance_metrics (
    metric_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name       TEXT NOT NULL,
    metric_date      DATE NOT NULL,
    runs_total       INT NOT NULL DEFAULT 0,
    runs_success     INT NOT NULL DEFAULT 0,
    runs_failure     INT NOT NULL DEFAULT 0,
    avg_duration_ms  INT,
    p95_duration_ms  INT,
    total_cost_usd   NUMERIC(10,4) DEFAULT 0,
    hitl_rejections  INT NOT NULL DEFAULT 0,
    hitl_approvals   INT NOT NULL DEFAULT 0,
    regression_flags JSONB DEFAULT '[]'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_name, metric_date)
);
CREATE INDEX IF NOT EXISTS idx_perf_metrics_date ON agent_performance_metrics (metric_date DESC);

-- ─── api_call_log (owned by: all agents via omerion_core.clients) ──
-- Per-call log for rate-limit and incident forensics
CREATE TABLE IF NOT EXISTS api_call_log (
    call_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name     TEXT NOT NULL,
    service        TEXT NOT NULL,   -- 'anthropic' | 'openai' | 'supabase' | 'pinecone' | 'gmail' | ...
    endpoint       TEXT,
    started_at     TIMESTAMPTZ NOT NULL,
    ended_at       TIMESTAMPTZ,
    status_code    INT,
    attempt        INT NOT NULL DEFAULT 1,
    retried_after_ms INT,
    cost_usd       NUMERIC(10,6) DEFAULT 0,
    error_class    TEXT,
    correlation_id UUID,
    metadata       JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_api_log_service_time ON api_call_log (service, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_log_agent_time ON api_call_log (agent_name, started_at DESC);

-- ─── rd_insights (owned by: R1 Market/Tech Watcher) ────────────────
CREATE TABLE IF NOT EXISTS rd_insights (
    insight_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_url       TEXT NOT NULL,
    source_type      TEXT NOT NULL,   -- 'rss' | 'github_release' | 'newsletter' | 'blog' | ...
    title            TEXT NOT NULL,
    summary          TEXT NOT NULL,
    impact_tag       TEXT NOT NULL,   -- 'daam' | 'oria' | 'rora' | 'asap' | 'internal_os'
    estimated_priority TEXT NOT NULL, -- 'high' | 'medium' | 'low'
    raw_content      TEXT,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_by_r3   BOOLEAN NOT NULL DEFAULT false,
    metadata         JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_insights_priority_time ON rd_insights (estimated_priority, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_insights_impact ON rd_insights (impact_tag, ingested_at DESC);

-- ─── oss_candidates (owned by: R2 OSS Scout) ───────────────────────
CREATE TABLE IF NOT EXISTS oss_candidates (
    candidate_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    repo_url         TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    stars            INT,
    last_commit_at   TIMESTAMPTZ,
    license          TEXT,
    architecture_summary TEXT NOT NULL,
    key_components   JSONB DEFAULT '[]'::jsonb,
    rubric_scores    JSONB NOT NULL,  -- {architecture_quality, complexity, community_velocity, ...}
    integration_type TEXT NOT NULL,   -- 'component' | 'pattern' | 'full_module' | 'reference_only'
    integration_recommendation TEXT,
    risk_notes       TEXT,
    evaluated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_by_r3   BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS idx_oss_candidates_recent ON oss_candidates (evaluated_at DESC);

-- ─── rd_proposals (owned by: R3 Strategic Architect) ───────────────
CREATE TABLE IF NOT EXISTS rd_proposals (
    proposal_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title              TEXT NOT NULL,
    problem            TEXT NOT NULL,
    proposed_change    TEXT NOT NULL,
    affected_modules   TEXT[] NOT NULL,
    breaking_changes   TEXT,
    test_plan          TEXT,
    rollout_strategy   TEXT,
    doc_url            TEXT,
    impact_score       TEXT NOT NULL,   -- 'low' | 'medium' | 'high'
    effort_score       TEXT NOT NULL,   -- 'S' | 'M' | 'L' | 'XL'
    priority_score     NUMERIC(5,4),
    source_insight_ids UUID[] DEFAULT '{}',
    source_oss_ids     UUID[] DEFAULT '{}',
    status             rd_proposal_status NOT NULL DEFAULT 'draft',
    founder_decision_notes TEXT,
    founder_decided_at TIMESTAMPTZ,
    handoff_blueprint_id UUID REFERENCES blueprints(blueprint_id) ON DELETE SET NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rd_proposals_status ON rd_proposals (status);
CREATE INDEX IF NOT EXISTS idx_rd_proposals_priority ON rd_proposals (priority_score DESC);


-- ─── 0006_functions.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0006 — Helper functions: emit_event, updated_at triggers, locks
-- ════════════════════════════════════════════════════════════════════

-- ─── emit_event — canonical entry point for agents to publish events ─
CREATE OR REPLACE FUNCTION emit_event(
    p_type         TEXT,
    p_source_agent TEXT,
    p_payload      JSONB DEFAULT '{}'::jsonb,
    p_contact_id   UUID DEFAULT NULL,
    p_account_id   UUID DEFAULT NULL,
    p_correlation_id UUID DEFAULT NULL
) RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    v_event_id UUID;
BEGIN
    INSERT INTO events (type, source_agent, payload, contact_id, account_id, correlation_id)
    VALUES (p_type, p_source_agent, p_payload, p_contact_id, p_account_id,
            COALESCE(p_correlation_id, uuid_generate_v4()))
    RETURNING event_id INTO v_event_id;
    RETURN v_event_id;
END
$$;

-- ─── updated_at trigger helper ─────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END
$$;

-- Apply trigger to every table with updated_at
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'updated_at'
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_%I_updated_at ON %I; '
            'CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION set_updated_at();',
            tbl, tbl, tbl, tbl
        );
    END LOOP;
END
$$;

-- ─── pg_advisory_lock helpers (idempotent per-target locking) ──────
-- Used by CRM Nurture / Lead Scraper to guarantee only one session
-- works on a given lead / account at a time.
CREATE OR REPLACE FUNCTION try_lock_contact(p_contact_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN pg_try_advisory_lock(hashtextextended('contact:' || p_contact_id::text, 0));
END
$$;

CREATE OR REPLACE FUNCTION release_lock_contact(p_contact_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_unlock(hashtextextended('contact:' || p_contact_id::text, 0));
END
$$;

CREATE OR REPLACE FUNCTION try_lock_account(p_account_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN pg_try_advisory_lock(hashtextextended('account:' || p_account_id::text, 0));
END
$$;

CREATE OR REPLACE FUNCTION release_lock_account(p_account_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_unlock(hashtextextended('account:' || p_account_id::text, 0));
END
$$;

-- ─── HITL token generator — used by omerion_core.hitl ──────────────
CREATE OR REPLACE FUNCTION generate_hitl_tokens()
RETURNS TABLE(approve_token TEXT, reject_token TEXT)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY SELECT
        encode(gen_random_bytes(24), 'base64'),
        encode(gen_random_bytes(24), 'base64');
END
$$;

-- ─── Expire stale HITL reviews (called by R4 cron) ─────────────────
CREATE OR REPLACE FUNCTION expire_stale_hitl_reviews()
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INT;
BEGIN
    UPDATE founder_review_queue
       SET decision = 'expired'
     WHERE decision = 'pending'
       AND expires_at < now();
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$$;


-- ─── 0007_rls.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0007 — RLS baseline
-- Service role (used by all agents) bypasses RLS; anon role is denied.
-- ════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    tbl TEXT;
    tables TEXT[] := ARRAY[
        'markets','accounts','contacts','events','scores','opportunities',
        'blueprints','build_tasks','deployments','revenue_events','lead_conversions',
        'agent_actions','attribution_reports','outbound_communications',
        'nurture_sequences','contact_activity_log','research_dossiers',
        'generated_drafts','founder_review_queue','agent_telemetry',
        'agent_performance_metrics','api_call_log','rd_insights',
        'oss_candidates','rd_proposals'
    ];
BEGIN
    FOREACH tbl IN ARRAY tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', tbl);
        -- Service role gets full access (agents use service role)
        EXECUTE format(
            'DROP POLICY IF EXISTS service_all ON %I; '
            'CREATE POLICY service_all ON %I FOR ALL TO service_role USING (true) WITH CHECK (true);',
            tbl, tbl
        );
    END LOOP;
END
$$;


-- ─── 0008_schema_fixes.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0008 — Schema fixes: close gaps between Python agent code and DB schema
-- Adds missing columns identified during April 2026 audit.
-- All ALTER TABLE statements are idempotent via DO-IF-NOT-EXISTS blocks.
-- ════════════════════════════════════════════════════════════════════

-- ─── 1. contacts — add nurture-stage + stop-condition fields ────────
-- CRM Nurture (Agent #5) queries/writes these fields on contacts.

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS stage         opportunity_stage NOT NULL DEFAULT 'new_lead',
    ADD COLUMN IF NOT EXISTS last_touch_reference TEXT         NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS replied       BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS explicit_no   BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS signed_agreement BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS meeting_booked  BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_contacts_stage ON contacts (stage);

-- ─── 0. blueprints — add columns written by Meeting Intelligence ─────
-- meeting_intelligence/tools.py persists account_id, contact_id, ttwa,
-- confidence, and correlation_id which are absent from the original schema.

ALTER TABLE blueprints
    ADD COLUMN IF NOT EXISTS account_id      UUID REFERENCES accounts(account_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS contact_id      UUID REFERENCES contacts(contact_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS ttwa            JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS confidence      NUMERIC(5,4) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS correlation_id  UUID;

CREATE INDEX IF NOT EXISTS idx_blueprints_account ON blueprints (account_id);
CREATE INDEX IF NOT EXISTS idx_blueprints_contact ON blueprints (contact_id);

-- ─── 1b. contacts — add provenance fields written by Lead Scraper ────
-- Lead Scraper & Enricher (Agent #3) writes locale, source, source_url.

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS locale     TEXT,
    ADD COLUMN IF NOT EXISTS source     TEXT,
    ADD COLUMN IF NOT EXISTS source_url TEXT;

-- ─── 2. accounts — add denormalised market + pain_signal ────────────
-- CRM Nurture and ICP Scoring join accounts for these fields;
-- Market Mapper (Agent #1) populates them during account upsert.

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS market      TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS pain_signal TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_accounts_market ON accounts (market);

-- ─── 3. deployments — add correlation_id + relax NOT NULL ────────────
-- Build Orchestrator (Agent #9) inserts correlation_id;
-- modules_deployed and go_live_date now have safe defaults so the
-- initial insert can omit them and be updated later.

ALTER TABLE deployments
    ADD COLUMN IF NOT EXISTS correlation_id UUID;

ALTER TABLE deployments
    ALTER COLUMN modules_deployed SET DEFAULT '{}',
    ALTER COLUMN go_live_date     SET DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_deployments_correlation ON deployments (correlation_id);

-- ─── 4. build_tasks — add columns used by Build Orchestrator code ─────
-- The Python TaskSpec model writes all these fields.  Legacy columns
-- (github_issue_number, branch, name, spec_md) are kept for reference.

ALTER TABLE build_tasks
    ADD COLUMN IF NOT EXISTS deployment_id  UUID REFERENCES deployments(deployment_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS slug           TEXT,
    ADD COLUMN IF NOT EXISTS title          TEXT,
    ADD COLUMN IF NOT EXISTS phase          TEXT,
    ADD COLUMN IF NOT EXISTS rationale      TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS effort_days    FLOAT NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS depends_on     TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS issue_number   INT,
    ADD COLUMN IF NOT EXISTS branch_name    TEXT,
    ADD COLUMN IF NOT EXISTS pr_number      INT,
    ADD COLUMN IF NOT EXISTS notes          TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_build_tasks_deployment ON build_tasks (deployment_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_build_tasks_slug ON build_tasks (deployment_id, slug)
    WHERE slug IS NOT NULL;

-- ─── 5. agent_performance_metrics — add extended columns for R4 ──────
-- R4 writes window_days, success_rate, avg_cost_usd and token counts
-- in addition to the baseline columns already in the schema.

ALTER TABLE agent_performance_metrics
    ADD COLUMN IF NOT EXISTS window_days   INT NOT NULL DEFAULT 14,
    ADD COLUMN IF NOT EXISTS success_rate  NUMERIC(5,4),
    ADD COLUMN IF NOT EXISTS avg_cost_usd  NUMERIC(10,6),
    ADD COLUMN IF NOT EXISTS tokens_input  BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tokens_output BIGINT NOT NULL DEFAULT 0;


-- ─── 0009_re_pivot.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0009 — Real Estate consulting pivot
-- Extends core tables for the 9-persona RE taxonomy, consulting offer
-- packages (replacing product-module SKUs), per-client deliverables,
-- and client-mode Build Orchestrator runs.
--
-- Safe to re-run: all ADDs are IF NOT EXISTS where PG allows it.
-- ════════════════════════════════════════════════════════════════════

-- ─── persona enum: add 5 new RE values (keep old ones for backcompat) ─
-- Old: team_lead, investor, property_manager, wholesaler, brokerage, solo_agent
-- New: + brokerage_owner, high_volume_agent, transaction_attorney,
--        business_attorney, developer
-- PG enum values cannot be removed without recreating the type; we keep
-- the old values and treat `brokerage_owner` as the preferred replacement
-- for `brokerage`/`solo_agent`. ICP scoring and lead enrichment emit only
-- the new names going forward.
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'brokerage_owner';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'high_volume_agent';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'transaction_attorney';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'business_attorney';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'developer';

-- ─── accounts: RE-specific signals ──────────────────────────────────
ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS persona_tier         INT,
    ADD COLUMN IF NOT EXISTS tech_stack           JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS mls_volume_estimate  INT;

CREATE INDEX IF NOT EXISTS idx_accounts_persona_tier_num
    ON accounts (persona_tier);

-- ─── opportunities: consulting package + paired demo ─────────────────
-- Old `offer_modules TEXT[]` + `offer_tier TEXT` kept for historical rows;
-- new writes use `service_package` + `demo_reference`.
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS service_package  TEXT,
    ADD COLUMN IF NOT EXISTS demo_reference   TEXT,
    ADD COLUMN IF NOT EXISTS price_band       JSONB;

CREATE INDEX IF NOT EXISTS idx_opportunities_service_package
    ON opportunities (service_package);

-- ─── clients: onboarded accounts + per-client Drive folder ───────────
CREATE TABLE IF NOT EXISTS clients (
    client_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id        UUID REFERENCES accounts(account_id) ON DELETE SET NULL,
    opportunity_id    UUID REFERENCES opportunities(opportunity_id) ON DELETE SET NULL,
    name              TEXT NOT NULL,
    slug              TEXT UNIQUE,
    persona           persona,
    service_package   TEXT,
    drive_folder_id   TEXT,    -- Google Drive folder for per-client deliverables
    onboarded_at      TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'active',   -- active | paused | churned
    metadata          JSONB DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_clients_status ON clients (status);
CREATE INDEX IF NOT EXISTS idx_clients_persona ON clients (persona);

-- Backfill the FKs on existing tables now that `clients` exists.
-- Tables 0003 declared `client_id UUID` without a FK; add the constraint now.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'deployments' AND constraint_name = 'deployments_client_id_fkey'
    ) THEN
        ALTER TABLE deployments
            ADD CONSTRAINT deployments_client_id_fkey
            FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'revenue_events' AND constraint_name = 'revenue_events_client_id_fkey'
    ) THEN
        ALTER TABLE revenue_events
            ADD CONSTRAINT revenue_events_client_id_fkey
            FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'agent_actions' AND constraint_name = 'agent_actions_client_id_fkey'
    ) THEN
        ALTER TABLE agent_actions
            ADD CONSTRAINT agent_actions_client_id_fkey
            FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'attribution_reports' AND constraint_name = 'attribution_reports_client_id_fkey'
    ) THEN
        ALTER TABLE attribution_reports
            ADD CONSTRAINT attribution_reports_client_id_fkey
            FOREIGN KEY (client_id) REFERENCES clients(client_id) ON DELETE SET NULL;
    END IF;
END$$;

-- ─── build_tasks / deployments: internal vs client mode ──────────────
ALTER TABLE build_tasks
    ADD COLUMN IF NOT EXISTS mode      TEXT NOT NULL DEFAULT 'internal',
    ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES clients(client_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_build_tasks_mode ON build_tasks (mode);

ALTER TABLE deployments
    ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'internal';

CREATE INDEX IF NOT EXISTS idx_deployments_mode ON deployments (mode);

-- ─── attribution_reports: case-study draft from positive deltas ──────
ALTER TABLE attribution_reports
    ADD COLUMN IF NOT EXISTS case_study_draft TEXT,
    ADD COLUMN IF NOT EXISTS case_study_status TEXT DEFAULT 'none';
    -- case_study_status: none | draft | approved | published


-- ─── 0010_langgraph_checkpointer.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0010 — LangGraph PostgresSaver tables
--
-- Schema is owned by the `langgraph-checkpoint-postgres` library.
-- `PostgresSaver.setup()` is invoked at app boot from
-- `omerion_core.runtime.checkpointer.get_checkpointer()` and creates/
-- upgrades these tables idempotently. We re-declare them here for
-- auditability, Supabase RLS wiring, and disaster-recovery rebuilds
-- without booting the app.
--
-- Tables (as of langgraph-checkpoint-postgres 2.0+):
--   checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations
--
-- If the library's migration layer advances past what's declared here,
-- `PostgresSaver.setup()` applies the delta on the next boot — so this
-- file does NOT need to be hand-edited on library upgrades.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id             TEXT NOT NULL,
    checkpoint_ns         TEXT NOT NULL DEFAULT '',
    checkpoint_id         TEXT NOT NULL,
    parent_checkpoint_id  TEXT,
    type                  TEXT,
    checkpoint            JSONB NOT NULL,
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread
    ON checkpoints (thread_id, checkpoint_ns);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id      TEXT NOT NULL,
    checkpoint_ns  TEXT NOT NULL DEFAULT '',
    checkpoint_id  TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    idx            INT NOT NULL,
    channel        TEXT NOT NULL,
    type           TEXT,
    blob           BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id      TEXT NOT NULL,
    checkpoint_ns  TEXT NOT NULL DEFAULT '',
    channel        TEXT NOT NULL,
    version        TEXT NOT NULL,
    type           TEXT NOT NULL,
    blob           BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

-- RLS: the runtime uses the Supabase DATABASE_URL (direct connection)
-- with superuser-equivalent privileges, so RLS is intentionally OFF on
-- these tables. Do not expose them via the Supabase REST layer.


-- ─── 0011_openclaw_control.sql ─────────────────────────────────────
-- 0011_openclaw_control.sql
-- Phase 8: OpenClaw messaging control surface (default channel: Telegram).
-- Tracks (a) active chat sessions with the founder / team, and
-- (b) an audit log of every inbound + outbound message that crossed
-- the OpenClaw bridge (approve/reject/edit/triggers/status queries).

CREATE TABLE IF NOT EXISTS openclaw_sessions (
    session_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel          TEXT NOT NULL CHECK (channel IN ('telegram', 'whatsapp', 'imessage', 'signal', 'other')),
    chat_target      TEXT NOT NULL,              -- Telegram chat_id, WA phone, iMessage E.164, etc.
    display_name     TEXT,
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'revoked')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS openclaw_sessions_target_idx
    ON openclaw_sessions (chat_target);


CREATE TABLE IF NOT EXISTS openclaw_audit_log (
    entry_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID REFERENCES openclaw_sessions(session_id) ON DELETE SET NULL,
    direction        TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    skill            TEXT,                -- skill name (approve_hitl, pending_approvals, ...)
    review_id        UUID,                -- when the message ties to a HITL row
    message_text     TEXT NOT NULL,
    response_status  INT,                 -- HTTP status from the control-plane call, when applicable
    correlation_id   UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS openclaw_audit_log_session_idx
    ON openclaw_audit_log (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS openclaw_audit_log_review_idx
    ON openclaw_audit_log (review_id)
    WHERE review_id IS NOT NULL;


-- ─── 0012_discord_channel.sql ─────────────────────────────────────
-- 0012_discord_channel.sql
-- Extend the openclaw_sessions channel CHECK constraint to include 'discord'
-- (migrated from Telegram as the primary HITL surface).

ALTER TABLE openclaw_sessions
    DROP CONSTRAINT IF EXISTS openclaw_sessions_channel_check;

ALTER TABLE openclaw_sessions
    ADD CONSTRAINT openclaw_sessions_channel_check
    CHECK (channel IN ('discord', 'telegram', 'whatsapp', 'imessage', 'signal', 'other'));


-- ─── 0013_agent_runs.sql ─────────────────────────────────────
-- 0013_agent_runs.sql
-- Phase 9: durable agent run lifecycle.
--
-- The system previously had no single source of truth for "is this run still
-- going? did it succeed?" — lifecycle was implicit in `checkpoints` (graph
-- state) + `agent_telemetry` (per-node spans). This table closes that gap so
-- founder visibility, Discord completion callbacks, and dashboard "running
-- now" feeds all read from one place.
--
-- `thread_id` is the LangGraph PostgresSaver thread key (= run_id::text by
-- convention); `review_id` is set when the graph is paused on a HITL gate.

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name          TEXT        NOT NULL,
    thread_id           TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','hitl_waiting','completed','failed','cancelled')),
    source_channel      TEXT        NOT NULL
        CHECK (source_channel IN ('discord','openclaw','scheduler','api')),
    triggered_by        TEXT,
    inputs              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    discord_channel_id  TEXT,
    discord_thread_id   TEXT,
    correlation_id      UUID,
    review_id           UUID        REFERENCES founder_review_queue(review_id) ON DELETE SET NULL,
    result_summary      TEXT,
    error               TEXT,
    cost_usd            NUMERIC(10,4),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS agent_runs_status_idx
    ON agent_runs (status);
CREATE INDEX IF NOT EXISTS agent_runs_agent_idx
    ON agent_runs (agent_name, created_at DESC);
CREATE INDEX IF NOT EXISTS agent_runs_thread_idx
    ON agent_runs (thread_id);
CREATE INDEX IF NOT EXISTS agent_runs_review_idx
    ON agent_runs (review_id)
    WHERE review_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_runs_correlation_idx
    ON agent_runs (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- Dashboard subscribes via Supabase realtime to drive the "running now" feed.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime' AND tablename = 'agent_runs'
    ) THEN
        EXECUTE 'ALTER PUBLICATION supabase_realtime ADD TABLE agent_runs';
    END IF;
END $$;


-- ─── 0014_telemetry_correlation_id.sql ─────────────────────────────────────
-- 0014_telemetry_correlation_id.sql
-- Phase 9: stamp `agent_telemetry` rows with the same correlation_id that
-- the run lifecycle, events bus, HITL queue, and OpenClaw audit log all
-- already carry. Lets a single id join across every per-node span, every
-- emitted event, and the agent_runs row for one execution.

ALTER TABLE agent_telemetry
    ADD COLUMN IF NOT EXISTS correlation_id UUID;

CREATE INDEX IF NOT EXISTS idx_telemetry_correlation
    ON agent_telemetry (correlation_id)
    WHERE correlation_id IS NOT NULL;


-- ─── 0015_research_dossiers_unique_account.sql ─────────────────────────────────────
-- 0015_research_dossiers_unique_account.sql
-- Phase A C3: enable idempotent dossier writes via upsert(on_conflict="account_id").
-- Today the schema permits multiple dossiers per account; semantically each
-- account has a single living dossier that gets refreshed. Collapse duplicates
-- (keep newest) and add the constraint that lets the upsert work.

WITH ranked AS (
    SELECT dossier_id,
           ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY created_at DESC) AS rn
    FROM research_dossiers
    WHERE account_id IS NOT NULL
)
DELETE FROM research_dossiers
WHERE dossier_id IN (SELECT dossier_id FROM ranked WHERE rn > 1);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'research_dossiers_account_id_unique'
    ) THEN
        ALTER TABLE research_dossiers
            ADD CONSTRAINT research_dossiers_account_id_unique UNIQUE (account_id);
    END IF;
END$$;


-- ─── 0016_cost_and_outcome_accounting.sql ─────────────────────────────────────
-- 0016_cost_and_outcome_accounting.sql
-- Phase D: Cost + outcome accounting per Boris+Elon B3/B4 recommendation.
-- Per-run cost columns on agent_runs + a business_outcomes table that ties
-- "money/meeting facts" back to the run that produced them via correlation_id.
-- Mission Control view aggregates the three numbers Elon demanded:
--   outcomes today, error count, total cost.

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS completion_tokens INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS llm_cost_usd   NUMERIC(10,6) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tool_call_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS business_outcomes (
    outcome_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    outcome_type    TEXT NOT NULL,
    -- The run that produced this outcome. Nullable because some outcomes
    -- (e.g. inbound replies) arrive via webhook before any run row exists.
    run_id          UUID REFERENCES agent_runs(run_id) ON DELETE SET NULL,
    correlation_id  TEXT,
    contact_id      UUID,
    account_id      UUID,
    opportunity_id  UUID,
    value_usd       NUMERIC(12,2),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT business_outcomes_type_chk CHECK (
        outcome_type IN (
            'qualified_lead', 'booked_demo', 'proposal_sent',
            'signed_contract', 'closed_won', 'closed_lost',
            'reply_received', 'meeting_completed'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_business_outcomes_type_time
    ON business_outcomes (outcome_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_business_outcomes_run
    ON business_outcomes (run_id);
CREATE INDEX IF NOT EXISTS idx_business_outcomes_correlation
    ON business_outcomes (correlation_id)
    WHERE correlation_id IS NOT NULL;

-- Mission Control: 3-row summary the dashboard reads to answer "is the system
-- working today?" without scanning agent_runs / agent_telemetry directly.
CREATE OR REPLACE VIEW mission_control_today AS
WITH today_runs AS (
    SELECT *
    FROM agent_runs
    WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')
)
SELECT
    (SELECT COUNT(*) FROM business_outcomes
     WHERE occurred_at >= date_trunc('day', now() AT TIME ZONE 'UTC')) AS outcomes_today,
    (SELECT COUNT(*) FROM today_runs WHERE status = 'failed')          AS errors_today,
    (SELECT COALESCE(SUM(llm_cost_usd), 0) FROM today_runs)            AS cost_usd_today,
    (SELECT COUNT(*) FROM today_runs WHERE status = 'completed')       AS completed_today,
    (SELECT COUNT(*) FROM today_runs WHERE status = 'running')         AS in_flight_now,
    (SELECT COUNT(*) FROM today_runs WHERE status = 'hitl_waiting')    AS hitl_waiting_now;

-- Add to realtime publication so the dashboard sees outcomes as they arrive.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'public'
          AND tablename = 'business_outcomes'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE business_outcomes;
    END IF;
END$$;


-- ─── 0017_seek_tables.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0017 — SEEK agent: job_postings + job_applications
-- ════════════════════════════════════════════════════════════════════

DO $$
BEGIN
  CREATE TYPE job_platform AS ENUM ('upwork', 'linkedin_jobs', 'indeed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  CREATE TYPE job_application_status AS ENUM (
    'discovered', 'drafted', 'queued_for_sender', 'email_queued',
    'sent', 'replied', 'ghosted', 'rejected', 'withdrawn'
  );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  CREATE TYPE job_budget_type AS ENUM ('hourly', 'fixed', 'salary', 'unknown');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── job_postings ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_postings (
    posting_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform         job_platform NOT NULL,
    external_id      TEXT NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'posting',   -- 'posting' | 'outreach_target'
    title            TEXT NOT NULL,
    company          TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL,
    target_name      TEXT NOT NULL DEFAULT '',
    target_title     TEXT NOT NULL DEFAULT '',
    budget_low       NUMERIC(12,2),
    budget_high      NUMERIC(12,2),
    budget_type      job_budget_type NOT NULL DEFAULT 'unknown',
    location         TEXT NOT NULL DEFAULT '',
    remote           BOOLEAN NOT NULL DEFAULT true,
    posted_at        TIMESTAMPTZ,
    relevance_score  NUMERIC(5,4) DEFAULT 0,
    pinecone_id      TEXT,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_job_postings_platform_score
    ON job_postings (platform, relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_job_postings_posted_at
    ON job_postings (posted_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_job_postings_kind
    ON job_postings (kind, platform);

-- ─── job_applications ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_applications (
    application_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    posting_id       UUID NOT NULL REFERENCES job_postings(posting_id) ON DELETE CASCADE,
    platform         job_platform NOT NULL,
    kind             TEXT NOT NULL DEFAULT 'posting',
    status           job_application_status NOT NULL DEFAULT 'drafted',
    cover_letter     TEXT NOT NULL DEFAULT '',
    outreach_message TEXT NOT NULL DEFAULT '',
    proposal_body    TEXT NOT NULL DEFAULT '',
    subject_line     TEXT NOT NULL DEFAULT '',
    resume_version   TEXT NOT NULL DEFAULT 'v1',
    submitted_at     TIMESTAMPTZ,
    replied_at       TIMESTAMPTZ,
    ghosted_at       TIMESTAMPTZ,
    rejection_reason TEXT,
    provider_ref     TEXT,           -- Gmail Message-ID or Upwork proposal ID
    review_id        UUID,
    run_id           UUID,
    correlation_id   UUID,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (posting_id, resume_version)
);

CREATE INDEX IF NOT EXISTS idx_job_applications_status
    ON job_applications (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_applications_posting
    ON job_applications (posting_id);
CREATE INDEX IF NOT EXISTS idx_job_applications_platform_status
    ON job_applications (platform, status);

-- Enable realtime for founder dashboard visibility.
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication pub
    JOIN pg_publication_rel pr ON pr.prpubid = pub.oid
    JOIN pg_class cls ON cls.oid = pr.prrelid
    JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
    WHERE pub.pubname = 'supabase_realtime' AND nsp.nspname = 'public' AND cls.relname = 'job_postings'
  ) THEN ALTER PUBLICATION supabase_realtime ADD TABLE public.job_postings; END IF;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication pub
    JOIN pg_publication_rel pr ON pr.prpubid = pub.oid
    JOIN pg_class cls ON cls.oid = pr.prrelid
    JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
    WHERE pub.pubname = 'supabase_realtime' AND nsp.nspname = 'public' AND cls.relname = 'job_applications'
  ) THEN ALTER PUBLICATION supabase_realtime ADD TABLE public.job_applications; END IF;
END $$;


-- ─── 0018_outreach_threads.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0018 — RAG Traction System: outreach_threads cross-channel tracking
-- ════════════════════════════════════════════════════════════════════
--
-- One row per contact. Tracks all cross-channel touch counts, response
-- detection, and ghost escalation state. Written by REACH + NURTURE on
-- every send. Read by the ghost_detector (daily) and response tracker
-- (every 2h).
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS outreach_threads (
    thread_id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    contact_id                  UUID NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,

    -- Lifecycle timestamps
    first_touch_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_touch_at               TIMESTAMPTZ,

    -- Per-channel touch counts (maintained by application layer)
    touch_count_email           INT NOT NULL DEFAULT 0,
    touch_count_sms             INT NOT NULL DEFAULT 0,
    touch_count_linkedin        INT NOT NULL DEFAULT 0,
    touch_count_total           INT NOT NULL DEFAULT 0,

    -- Response tracking
    response_received           BOOLEAN NOT NULL DEFAULT false,
    response_at                 TIMESTAMPTZ,
    response_channel            outreach_channel,   -- uses existing enum from 0001

    -- Ghost tracking
    ghost_declared              BOOLEAN NOT NULL DEFAULT false,
    ghost_declared_at           TIMESTAMPTZ,
    ghost_outcome               TEXT,               -- 're_engage' | 'escalate_to_hitl' | 'archive'
    reengagement_scheduled_at   TIMESTAMPTZ,
    reengagement_strategy       TEXT,               -- 'switch_channel' | 'founder_personal' | 'do_not_contact'

    metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One thread per contact; upsert on contact_id conflict.
    UNIQUE (contact_id)
);

-- Efficient polling by ghost_detector (scans only unresolved threads).
CREATE INDEX IF NOT EXISTS idx_outreach_threads_ghost_scan
    ON outreach_threads (last_touch_at, touch_count_total)
    WHERE ghost_declared = false AND response_received = false;

-- Efficient polling by response_tracker (already-responded are excluded).
CREATE INDEX IF NOT EXISTS idx_outreach_threads_contact
    ON outreach_threads (contact_id);

-- Enable realtime for dashboard.
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication pub
    JOIN pg_publication_rel pr ON pr.prpubid = pub.oid
    JOIN pg_class cls ON cls.oid = pr.prrelid
    JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace
    WHERE pub.pubname = 'supabase_realtime' AND nsp.nspname = 'public' AND cls.relname = 'outreach_threads'
  ) THEN ALTER PUBLICATION supabase_realtime ADD TABLE public.outreach_threads; END IF;
END $$;


-- ─── 0019_seek_extra_platforms.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0019 — SEEK: extend job_platform enum + add posting metadata fields
-- ════════════════════════════════════════════════════════════════════

-- Tier-S invite-only freelance networks
ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'toptal';
ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'ateam';
ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'braintrust';
ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'contra';

-- Tier-A high-signal startup / proptech employer boards
ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'wellfound';
ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'yc';
ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'lever';
ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'greenhouse';

-- New job_postings columns parsed at discovery time
ALTER TABLE job_postings
    ADD COLUMN IF NOT EXISTS application_deadline TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS required_skills      JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS rank_score           NUMERIC(4,2) DEFAULT 0.0,
    ADD COLUMN IF NOT EXISTS rank_rationale       TEXT;

-- New job_applications columns surfaced from the HITL flag pass
ALTER TABLE job_applications
    ADD COLUMN IF NOT EXISTS hitl_flags  JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS hitl_notes  TEXT;

-- Index for duplicate-company flag detection (past 30 days lookback)
CREATE INDEX IF NOT EXISTS job_applications_company_recent_idx
    ON job_applications (submitted_at DESC)
    WHERE status IN ('sent', 'replied');


-- ─── 0020_seek_google_jobs_platform.sql ─────────────────────────────────────
-- Migration: add google_jobs to job_platform enum
-- Google Jobs (via SerpAPI) aggregates Indeed, LinkedIn, Glassdoor, ZipRecruiter
-- and company career pages into one structured source.

ALTER TYPE job_platform ADD VALUE IF NOT EXISTS 'google_jobs';


-- ─── 0021_knowledge_base.sql ─────────────────────────────────────
-- 0021_knowledge_base.sql
-- Knowledge Base ingestion pipeline tables.
-- Idempotent: all statements use IF NOT EXISTS / OR REPLACE.

-- pgvector must be enabled (was added in 0001 alongside uuid-ossp etc.)
CREATE EXTENSION IF NOT EXISTS vector;

-- ── document_chunks ──────────────────────────────────────────────────────────
-- Stores chunked + embedded content from Google Drive Knowledge Base documents.
-- embedding is vector(1536) matching OpenAI text-embedding-3-small.

CREATE TABLE IF NOT EXISTS document_chunks (
    id           uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id      text        NOT NULL,
    chunk_index  int         NOT NULL,
    content      text        NOT NULL,
    embedding    vector(1536),
    metadata     jsonb,
    created_at   timestamptz DEFAULT now(),
    UNIQUE (file_id, chunk_index)
);

-- HNSW index for fast approximate nearest-neighbour cosine search.
-- m=16, ef_construction=64 balances recall and build time for KB-scale data.
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
    ON document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS document_chunks_file_id_idx
    ON document_chunks (file_id);

-- ── document_index ───────────────────────────────────────────────────────────
-- Deduplication + audit log. One row per Drive file.
-- content_hash (sha256 of full text) is compared before re-embedding.

CREATE TABLE IF NOT EXISTS document_index (
    id             uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    file_id        text        NOT NULL UNIQUE,
    file_name      text        NOT NULL,
    content_hash   text        NOT NULL,
    chunk_count    int         NOT NULL,
    mime_type      text,
    last_ingested  timestamptz DEFAULT now(),
    status         text        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    error_message  text
);

-- ── drive_watch_channels ──────────────────────────────────────────────────────
-- Tracks active Google Drive push-notification channels for auto-renewal.

CREATE TABLE IF NOT EXISTS drive_watch_channels (
    channel_id   text        PRIMARY KEY,
    resource_id  text        NOT NULL,
    expires_at   timestamptz NOT NULL,
    folder_id    text        NOT NULL,
    created_at   timestamptz DEFAULT now()
);

-- ── match_documents RPC ───────────────────────────────────────────────────────
-- Semantic similarity search over document_chunks.
-- Called by agents that need to query the Knowledge Base at inference time.

CREATE OR REPLACE FUNCTION match_documents(
    query_embedding  vector(1536),
    match_threshold  float   DEFAULT 0.7,
    match_count      int     DEFAULT 10,
    filter_file_id   text    DEFAULT NULL
)
RETURNS TABLE (
    id           uuid,
    file_id      text,
    chunk_index  int,
    content      text,
    metadata     jsonb,
    similarity   float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.file_id,
        dc.chunk_index,
        dc.content,
        dc.metadata,
        (1 - (dc.embedding <=> query_embedding))::float AS similarity
    FROM document_chunks dc
    WHERE
        (filter_file_id IS NULL OR dc.file_id = filter_file_id)
        AND (1 - (dc.embedding <=> query_embedding)) > match_threshold
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;


-- ─── 0022_properties.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0022 — properties: HomeHarvest (Realtor.com) ingest table
-- ════════════════════════════════════════════════════════════════════
-- Stores normalized real estate listings scraped via the homeharvest
-- Python library (https://github.com/ZacharyHampton/HomeHarvest).
-- Used by SCOUT (lead_scraper_enricher) to surface properties for
-- downstream ICP scoring, dossier generation, and outreach targeting.

DO $$
BEGIN
  CREATE TYPE property_listing_status AS ENUM ('for_sale', 'for_rent', 'sold', 'pending');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS properties (
    property_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source             TEXT NOT NULL DEFAULT 'realtor.com',
    external_id        TEXT NOT NULL,                       -- HomeHarvest property_id / mls_id
    listing_status     property_listing_status NOT NULL,
    address            TEXT NOT NULL DEFAULT '',
    city               TEXT NOT NULL DEFAULT '',
    state              TEXT NOT NULL DEFAULT '',
    zip_code           TEXT NOT NULL DEFAULT '',
    list_price         NUMERIC(14,2),
    sold_price         NUMERIC(14,2),
    beds               INTEGER,
    baths_full         INTEGER,
    baths_half         INTEGER,
    sqft               INTEGER,
    lot_sqft           INTEGER,
    year_built         INTEGER,
    style              TEXT NOT NULL DEFAULT '',            -- single_family | condo | townhouse | ...
    list_date          DATE,
    sold_date          DATE,
    days_on_market     INTEGER,
    primary_photo_url  TEXT NOT NULL DEFAULT '',
    listing_url        TEXT NOT NULL DEFAULT '',
    agent_name         TEXT NOT NULL DEFAULT '',
    agent_email        TEXT NOT NULL DEFAULT '',
    agent_phone        TEXT NOT NULL DEFAULT '',
    broker_name        TEXT NOT NULL DEFAULT '',
    raw                JSONB NOT NULL DEFAULT '{}'::jsonb,  -- full HomeHarvest row preserved
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_properties_status_city
    ON properties (listing_status, city);
CREATE INDEX IF NOT EXISTS idx_properties_list_date
    ON properties (list_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_properties_agent_email
    ON properties (agent_email)
    WHERE agent_email <> '';
CREATE INDEX IF NOT EXISTS idx_properties_zip
    ON properties (zip_code);


-- ─── 0023_agent_messages.sql ─────────────────────────────────────
-- 0023_agent_messages.sql
-- Persistent log of agent-to-agent handoff narration and system notifications.
-- Replaces tmp/rq_notified.json and tmp/digest_sent.json with a proper DB table.
-- Feeds: Discord #omerion-room narration, dashboard team-chat panel, HITL dedup.

CREATE TABLE IF NOT EXISTS agent_messages (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      uuid        REFERENCES agent_runs(run_id) ON DELETE SET NULL,
    from_agent  text        NOT NULL,
    to_agent    text,
    message     text        NOT NULL,
    event_type  text,
    meta        jsonb       NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_created_at  ON agent_messages (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_from_agent  ON agent_messages (from_agent);
CREATE INDEX IF NOT EXISTS idx_agent_messages_event_type  ON agent_messages (event_type);


-- ─── 0024_error_log.sql ─────────────────────────────────────
-- 0024_error_log.sql
-- Non-run error log: bot disconnects, scheduler crashes, webhook failures.
-- Agent-run errors stay in agent_runs.status='error'.
-- Feeds: dashboard Error panel (Live / 24h / 7d / 30d filter).

CREATE TABLE IF NOT EXISTS error_log (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source      text        NOT NULL,   -- 'omerion_bot' | 'scheduler' | 'webhook' | 'discord_route'
    message     text        NOT NULL,
    traceback   text,
    meta        jsonb       NOT NULL DEFAULT '{}',
    occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_error_log_occurred_at  ON error_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_error_log_source       ON error_log (source);


-- ─── 0025_drop_openclaw.sql ─────────────────────────────────────
-- ════════════════════════════════════════════════════════════════════
-- 0025 — Drop OpenClaw: tables, data migration, constraint update
-- ════════════════════════════════════════════════════════════════════
-- All OpenClaw functionality is replaced by the first-party Discord
-- bot in discord/omerion_bot.py. This migration is safe to re-run.

-- ── Step 1: Drop OpenClaw-specific tables ─────────────────────────
DROP TABLE IF EXISTS openclaw_sessions  CASCADE;
DROP TABLE IF EXISTS openclaw_audit_log CASCADE;

-- ── Step 2: Migrate historical data ───────────────────────────────
-- Any agent_runs rows that were triggered via OpenClaw have
-- source_channel = 'openclaw'. Reclassify them as 'discord' because
-- OpenClaw was purely a Discord relay — the originating channel was
-- always Discord.
UPDATE public.agent_runs
    SET source_channel = 'discord'
    WHERE source_channel = 'openclaw';

-- Also catch any other unexpected values that would block the
-- constraint — map them to 'api' as a safe fallback.
UPDATE public.agent_runs
    SET source_channel = 'api'
    WHERE source_channel NOT IN ('discord', 'scheduler', 'api', 'event');

-- ── Step 3: Replace source_channel CHECK constraint ───────────────
-- Drop the old constraint (may include 'openclaw' or not exist yet).
ALTER TABLE public.agent_runs
    DROP CONSTRAINT IF EXISTS agent_runs_source_channel_check;

-- Add the new constraint. All rows are now in the allowed set.
ALTER TABLE public.agent_runs
    ADD CONSTRAINT agent_runs_source_channel_check
    CHECK (source_channel IN ('discord', 'scheduler', 'api', 'event'));


-- ── 0031: competitor_battle_cards ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS competitor_battle_cards (
    signal_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    competitor   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'other',
    title        TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    url          TEXT UNIQUE,
    impact       TEXT NOT NULL DEFAULT 'low',
    published_at TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS battle_cards_competitor_idx
    ON competitor_battle_cards (competitor, created_at DESC);

CREATE INDEX IF NOT EXISTS battle_cards_impact_idx
    ON competitor_battle_cards (impact)
    WHERE impact IN ('medium', 'high');

-- ── 0032: agent_runs client_id ─────────────────────────────────────────────
ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES clients(client_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS agent_runs_client_idx
    ON agent_runs (client_id, created_at DESC)
    WHERE client_id IS NOT NULL;

-- ── 0033: agent_config (r4 auto-pause kill switch) ─────────────────────────
CREATE TABLE IF NOT EXISTS agent_config (
    agent_name TEXT PRIMARY KEY,
    agent_schedule_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    persona_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    paused_at TIMESTAMPTZ,
    paused_reason TEXT,
    paused_by TEXT,
    client_id UUID REFERENCES clients(client_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_config_enabled
    ON agent_config (agent_schedule_enabled)
    WHERE agent_schedule_enabled = FALSE;

CREATE INDEX IF NOT EXISTS idx_agent_config_client
    ON agent_config (client_id)
    WHERE client_id IS NOT NULL;

INSERT INTO agent_config (agent_name) VALUES
    ('biz_dev_outreach'), ('build_orchestrator'), ('client_onboarding'),
    ('client_success'), ('competitive_intel'), ('crm_nurture'),
    ('high_quality_lead_scraping'), ('icp_scoring'), ('lead_scraper_enricher'),
    ('linkedin_outreach'), ('market_mapper'), ('meeting_intelligence'),
    ('offer_matching'), ('outcome_attribution'), ('r1_market_tech_watcher'),
    ('r2_oss_scout'), ('r3_strategic_architect'), ('r4_evaluation_telemetry')
ON CONFLICT (agent_name) DO NOTHING;

-- ── 0034: state_change_log (immutable audit trail) ────────────────────────────
CREATE TABLE IF NOT EXISTS state_change_log (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    agent_name TEXT NOT NULL DEFAULT '',
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_state_change_log_run_id
    ON state_change_log (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_state_change_log_agent_recent
    ON state_change_log (agent_name, created_at DESC);

-- ── 0035: system_mutex + agent_runs.superseded_at ─────────────────────────────
CREATE TABLE IF NOT EXISTS system_mutex (
    lock_name   TEXT PRIMARY KEY,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    acquired_by TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_system_mutex_expires_at
    ON system_mutex (expires_at);

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_agent_runs_superseded
    ON agent_runs (superseded_at)
    WHERE superseded_at IS NOT NULL;

CREATE OR REPLACE FUNCTION try_acquire_mutex(
    p_lock_name   TEXT,
    p_ttl_seconds INTEGER,
    p_holder_id   TEXT
) RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_holder TEXT;
BEGIN
    INSERT INTO system_mutex (lock_name, acquired_at, acquired_by, expires_at)
    VALUES (p_lock_name, now(), p_holder_id, now() + make_interval(secs => p_ttl_seconds))
    ON CONFLICT (lock_name) DO UPDATE
        SET acquired_at = EXCLUDED.acquired_at,
            acquired_by = EXCLUDED.acquired_by,
            expires_at  = EXCLUDED.expires_at
        WHERE system_mutex.expires_at < now()
    RETURNING acquired_by INTO v_holder;

    IF v_holder IS NULL THEN
        SELECT acquired_by INTO v_holder FROM system_mutex WHERE lock_name = p_lock_name;
    END IF;
    RETURN v_holder;
END;
$$;

-- ── 0036: client post-sale pipeline stage tracking ─────────────────────────────
DO $$ BEGIN
    CREATE TYPE client_pipeline_stage AS ENUM (
        'signed', 'kickoff', 'in_delivery', 'success', 'renewal_due', 'churned'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS pipeline_stage client_pipeline_stage NOT NULL DEFAULT 'signed';
CREATE INDEX IF NOT EXISTS idx_clients_pipeline_stage ON clients (pipeline_stage);

CREATE TABLE IF NOT EXISTS client_stage_history (
    id BIGSERIAL PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES clients(client_id) ON DELETE CASCADE,
    from_stage client_pipeline_stage,
    to_stage client_pipeline_stage NOT NULL,
    changed_by TEXT,
    notes TEXT,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_client_stage_history_client ON client_stage_history (client_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_client_stage_history_to_stage ON client_stage_history (to_stage, created_at DESC);

CREATE OR REPLACE FUNCTION _log_client_pipeline_stage_change()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.pipeline_stage IS DISTINCT FROM OLD.pipeline_stage THEN
        INSERT INTO client_stage_history (client_id, from_stage, to_stage)
        VALUES (NEW.client_id, OLD.pipeline_stage, NEW.pipeline_stage);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_log_client_pipeline_stage_change ON clients;
CREATE TRIGGER trg_log_client_pipeline_stage_change
    BEFORE UPDATE ON clients
    FOR EACH ROW
    EXECUTE FUNCTION _log_client_pipeline_stage_change();

-- ─── 0045: builder_task_status — add BUILDER status values ──────────────────
ALTER TYPE build_task_status ADD VALUE IF NOT EXISTS 'branch_open';
ALTER TYPE build_task_status ADD VALUE IF NOT EXISTS 'pr_open';
ALTER TYPE build_task_status ADD VALUE IF NOT EXISTS 'failed';

-- Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
-- ==============================================================================
-- 0063_newsletter_subscribers.sql
-- Description: Creates the newsletter_subscribers table for the Base44 opt-in flow
-- ==============================================================================

CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    subscriber_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name                   TEXT NOT NULL,
    email                  TEXT NOT NULL UNIQUE,
    industry               TEXT NOT NULL,
    role                   TEXT,
    status                 TEXT NOT NULL DEFAULT 'active', -- active | unsubscribed
    last_skillpack_sent_at TIMESTAMPTZ,
    last_playbook_sent_at  TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for cron jobs searching for due subscribers by industry
CREATE INDEX IF NOT EXISTS idx_newsletter_subscribers_industry ON newsletter_subscribers (industry, status);
CREATE INDEX IF NOT EXISTS idx_newsletter_subscribers_skillpack ON newsletter_subscribers (last_skillpack_sent_at ASC NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_newsletter_subscribers_playbook ON newsletter_subscribers (last_playbook_sent_at ASC NULLS FIRST);

-- Updated_at trigger
DROP TRIGGER IF EXISTS trg_newsletter_subscribers_updated_at ON newsletter_subscribers;
CREATE TRIGGER trg_newsletter_subscribers_updated_at 
    BEFORE UPDATE ON newsletter_subscribers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- RLS Baseline
ALTER TABLE newsletter_subscribers ENABLE ROW LEVEL SECURITY;
-- Service role bypasses RLS
CREATE POLICY "Service role full access" ON newsletter_subscribers FOR ALL TO service_role USING (true) WITH CHECK (true);
