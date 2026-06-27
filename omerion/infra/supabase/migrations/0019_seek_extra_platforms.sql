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
