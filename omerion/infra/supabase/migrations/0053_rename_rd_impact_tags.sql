-- Migration 0053: Canonicalise impact_tag values across R&D tables.
-- Renames retired codenames oria->capa and rora->remi in rd_insights,
-- rd_oss_candidates, and rd_proposals to match the canonical demo_catalog keys
-- (daam/capa/remi/asap/internal_os).
--
-- Idempotent: re-running is a no-op once values are migrated.
-- MUST be run AFTER migration 0050 (which creates rd_oss_candidates).
-- Apply in Supabase SQL Editor; reload PostgREST schema after running.

BEGIN;

-- ── rd_insights ───────────────────────────────────────────────────────────────
ALTER TABLE rd_insights DROP CONSTRAINT IF EXISTS rd_insights_impact_tag_check;

UPDATE rd_insights SET impact_tag = 'capa' WHERE impact_tag = 'oria';
UPDATE rd_insights SET impact_tag = 'remi' WHERE impact_tag = 'rora';

ALTER TABLE rd_insights
    ADD CONSTRAINT rd_insights_impact_tag_check
    CHECK (impact_tag IN ('daam', 'capa', 'remi', 'asap', 'internal_os'));

-- ── rd_oss_candidates (created by migration 0050) ─────────────────────────────
ALTER TABLE rd_oss_candidates DROP CONSTRAINT IF EXISTS rd_oss_candidates_impact_tag_check;

UPDATE rd_oss_candidates SET impact_tag = 'capa' WHERE impact_tag = 'oria';
UPDATE rd_oss_candidates SET impact_tag = 'remi' WHERE impact_tag = 'rora';

ALTER TABLE rd_oss_candidates
    ADD CONSTRAINT rd_oss_candidates_impact_tag_check
    CHECK (impact_tag IN ('daam', 'capa', 'remi', 'asap', 'internal_os'));

-- ── rd_proposals ──────────────────────────────────────────────────────────────
-- Add target_module if a legacy schema omitted it, then rename retired values.
ALTER TABLE rd_proposals ADD COLUMN IF NOT EXISTS target_module TEXT;

ALTER TABLE rd_proposals DROP CONSTRAINT IF EXISTS rd_proposals_target_module_check;

UPDATE rd_proposals SET target_module = 'capa' WHERE target_module = 'oria';
UPDATE rd_proposals SET target_module = 'remi' WHERE target_module = 'rora';

-- NULL allowed: rows written before this column existed will have NULL target_module.
ALTER TABLE rd_proposals
    ADD CONSTRAINT rd_proposals_target_module_check
    CHECK (target_module IS NULL OR target_module IN ('daam', 'capa', 'remi', 'asap', 'internal_os'));

-- ── Reload PostgREST schema cache ─────────────────────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
