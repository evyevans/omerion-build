-- ════════════════════════════════════════════════════════════════════
-- 0026 — General Industry Pivot
-- Extends persona ENUM with 9 general business personas.
-- Removes Real Estate-specific columns and the properties table.
--
-- Safe to re-run: all ADDs are IF NOT EXISTS; DROPs are guarded.
-- RE ENUM values (brokerage_owner, high_volume_agent, etc.) are
-- NOT removed — PostgreSQL does not support DROP VALUE on ENUMs.
-- They are treated as permanently retired; all code emits only
-- the new general values going forward.
-- ════════════════════════════════════════════════════════════════════

-- ─── persona enum: create if not exists (self-contained guard) ───────
-- Needed when earlier migrations (0001) were never applied to this DB.
DO $$ BEGIN
    CREATE TYPE persona AS ENUM (
        'team_lead', 'investor', 'property_manager',
        'wholesaler', 'brokerage', 'solo_agent'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── persona enum: add 9 new general business personas ───────────────
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'ops_leader';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'revenue_leader';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'sme_founder';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'agency_owner';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'ecommerce_operator';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'professional_services_owner';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'saas_founder';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'hr_talent_leader';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'finance_ops';

-- ─── accounts: remove RE-specific column ─────────────────────────────
ALTER TABLE accounts
    DROP COLUMN IF EXISTS mls_volume_estimate;

-- ─── accounts: add general growth signal column ───────────────────────
ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS growth_signal_estimate INT;

CREATE INDEX IF NOT EXISTS idx_accounts_growth_signal
    ON accounts (growth_signal_estimate);

-- ─── properties: drop table and dependent type ───────────────────────
-- This table is 100% RE-specific (HomeHarvest / Realtor.com listings).
-- All dependent objects are CASCADE-dropped.
DROP TABLE IF EXISTS properties CASCADE;

-- Drop the property_listing_status ENUM that only served properties.
DROP TYPE IF EXISTS property_listing_status;

-- ─── Reload PostgREST schema cache ───────────────────────────────────
-- Required so the API layer sees the new ENUM values and dropped columns.
NOTIFY pgrst, 'reload schema';
