-- ═══════════════════════════════════════════════════════════════════════════
-- AUDITOR AGENT — Supabase Migration
-- Paste this entire block into Supabase → SQL Editor → Run
-- ═══════════════════════════════════════════════════════════════════════════


-- ─── TABLE 1: audit_log ──────────────────────────────────────────────────────
-- Every self-improvement action taken by HEALER, TRAINER, or any RSI agent
-- is written here. AUDITOR reads and marks records as audited/reverted.

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_agent            TEXT        NOT NULL,
    action_type             TEXT        NOT NULL,
    target_resource         TEXT        NOT NULL,
    diff_summary            TEXT        NOT NULL DEFAULT '',
    raw_payload             JSONB       NOT NULL DEFAULT '{}',
    hitl_review_id          UUID,                               -- soft ref to founder_review_queue
    triggering_event_id     TEXT,
    requires_git_revert     BOOLEAN     NOT NULL DEFAULT FALSE,
    audited                 BOOLEAN     NOT NULL DEFAULT FALSE,
    reverted                BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for AUDITOR's scan queries
CREATE INDEX IF NOT EXISTS idx_audit_log_audited
    ON audit_log (audited) WHERE audited = FALSE;

CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
    ON audit_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_source
    ON audit_log (source_agent);

CREATE INDEX IF NOT EXISTS idx_audit_log_event
    ON audit_log (triggering_event_id) WHERE triggering_event_id IS NOT NULL;

-- RLS: enable and lock down. Service role bypasses RLS automatically in Supabase.
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;

-- No policies = no public/anon access. Service role key bypasses RLS natively.


-- ─── TABLE 2: auditor_verdicts ───────────────────────────────────────────────
-- One row per audit_log entry. AUDITOR writes constitutional judgments here.
-- Upserted on audit_id — re-running AUDITOR on the same record is safe.

CREATE TABLE IF NOT EXISTS auditor_verdicts (
    verdict_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    audit_id                UUID        NOT NULL REFERENCES audit_log(audit_id) ON DELETE CASCADE,
    run_id                  TEXT        NOT NULL,
    severity                TEXT        NOT NULL
                                CHECK (severity IN ('compliant', 'suspicious', 'critical_violation')),
    rules_violated          TEXT[]      NOT NULL DEFAULT '{}',
    revert_executed         BOOLEAN     NOT NULL DEFAULT FALSE,
    revert_error            TEXT,
    verdict_reasoning       TEXT        NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_auditor_verdicts_audit_id UNIQUE (audit_id)
);

CREATE INDEX IF NOT EXISTS idx_auditor_verdicts_severity
    ON auditor_verdicts (severity);

CREATE INDEX IF NOT EXISTS idx_auditor_verdicts_run_id
    ON auditor_verdicts (run_id);

CREATE INDEX IF NOT EXISTS idx_auditor_verdicts_created_at
    ON auditor_verdicts (created_at DESC);

ALTER TABLE auditor_verdicts ENABLE ROW LEVEL SECURITY;


-- ─── TABLE 3: auditor_weekly_reports ─────────────────────────────────────────
-- One row per Monday compliance report. Insert-only historical record.

CREATE TABLE IF NOT EXISTS auditor_weekly_reports (
    report_id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    report_date                 DATE        NOT NULL,
    window_days                 INTEGER     NOT NULL DEFAULT 7,
    total_records_scanned       INTEGER     NOT NULL DEFAULT 0,
    compliant_count             INTEGER     NOT NULL DEFAULT 0,
    suspicious_count            INTEGER     NOT NULL DEFAULT 0,
    critical_violation_count    INTEGER     NOT NULL DEFAULT 0,
    reverted_count              INTEGER     NOT NULL DEFAULT 0,
    top_offending_agents        TEXT[]      NOT NULL DEFAULT '{}',
    narrative_md                TEXT        NOT NULL DEFAULT '',
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auditor_weekly_report_date
    ON auditor_weekly_reports (report_date DESC);

ALTER TABLE auditor_weekly_reports ENABLE ROW LEVEL SECURITY;


-- ─── Table comments ──────────────────────────────────────────────────────────

COMMENT ON TABLE audit_log IS
    'RSI agent self-improvement actions. Written by HEALER/TRAINER. '
    'Read and marked by AUDITOR (Agent #19 — Constitutional Guardian).';

COMMENT ON COLUMN audit_log.source_agent        IS 'Which agent took the action, e.g. "healer"';
COMMENT ON COLUMN audit_log.action_type         IS 'Verb: config_patch | prompt_update | agent_revert | etc.';
COMMENT ON COLUMN audit_log.target_resource     IS 'Path to modified resource, e.g. "config/agents.yaml"';
COMMENT ON COLUMN audit_log.diff_summary        IS 'Human-readable diff or change description (≤2000 chars)';
COMMENT ON COLUMN audit_log.raw_payload         IS 'Full JSON payload incl. old_value, new_value, config_key';
COMMENT ON COLUMN audit_log.hitl_review_id      IS 'UUID of founder_review_queue row if HITL was claimed';
COMMENT ON COLUMN audit_log.requires_git_revert IS 'True if change touched a versioned file, not just config';
COMMENT ON COLUMN audit_log.audited             IS 'Set true by AUDITOR after processing';
COMMENT ON COLUMN audit_log.reverted            IS 'Set true by AUDITOR after a successful revert';

COMMENT ON TABLE auditor_verdicts IS
    'Constitutional judgments per audit_log record. '
    'severity: compliant | suspicious | critical_violation. '
    'Upserted on audit_id — idempotent.';

COMMENT ON TABLE auditor_weekly_reports IS
    'Weekly compliance summaries generated every Monday. '
    'Insert-only. Each Monday creates a new historical row.';


-- ─── Verification query (run this separately to confirm tables were created) ─
-- SELECT table_name, pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) AS size
-- FROM information_schema.tables
-- WHERE table_schema = 'public'
--   AND table_name IN ('audit_log', 'auditor_verdicts', 'auditor_weekly_reports')
-- ORDER BY table_name;
