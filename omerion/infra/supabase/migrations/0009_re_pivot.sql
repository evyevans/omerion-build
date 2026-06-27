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
