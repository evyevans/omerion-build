-- ════════════════════════════════════════════════════════════════════
-- 0017 — SEEK agent: job_postings + job_applications
-- ════════════════════════════════════════════════════════════════════

DO $$ BEGIN
    CREATE TYPE job_platform AS ENUM ('upwork', 'linkedin_jobs', 'indeed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE job_application_status AS ENUM (
        'discovered',
        'drafted',
        'queued_for_sender',
        'email_queued',
        'sent',
        'replied',
        'ghosted',
        'rejected',
        'withdrawn'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE job_budget_type AS ENUM ('hourly', 'fixed', 'salary', 'unknown');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

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
ALTER PUBLICATION supabase_realtime ADD TABLE job_postings;
ALTER PUBLICATION supabase_realtime ADD TABLE job_applications;
