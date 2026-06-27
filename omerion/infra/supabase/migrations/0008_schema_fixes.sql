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
