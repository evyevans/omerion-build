-- Migration: 0066_newsletter_drive_file_id
-- Purpose: Make the Google Drive → newsletter_materials sync idempotent.
--          Each Drive file has a stable file id; storing it (UNIQUE) lets the
--          sync UPSERT on conflict instead of inserting duplicate rows on every
--          scheduler tick.

ALTER TABLE public.newsletter_materials
    ADD COLUMN IF NOT EXISTS drive_file_id TEXT;

-- created_at already exists (migration 0064); the recency / timeframe filter
-- ("uploaded within the last 2 weeks / month") reads it directly.

-- Idempotency key for the Drive sync. Partial unique index so legacy rows with
-- a NULL drive_file_id (hand-seeded) don't collide with each other.
CREATE UNIQUE INDEX IF NOT EXISTS uq_newsletter_materials_drive_file_id
    ON public.newsletter_materials(drive_file_id)
    WHERE drive_file_id IS NOT NULL;

-- Allow the weekly newsletter material_type (migration 0064 only permitted
-- 'skill_pack' and 'playbook'; the weekly newsletter cron uses 'newsletter').
ALTER TABLE public.newsletter_materials
    DROP CONSTRAINT IF EXISTS newsletter_materials_material_type_check;
ALTER TABLE public.newsletter_materials
    ADD CONSTRAINT newsletter_materials_material_type_check
    CHECK (material_type IN ('skill_pack', 'playbook', 'newsletter'));
