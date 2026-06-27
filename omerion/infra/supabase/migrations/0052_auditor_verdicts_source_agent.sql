-- Migration 0052: Denormalize source_agent into auditor_verdicts
-- Fixes: weekly report leaderboard always showing "unknown" (Q2 fix)
-- source_agent is copied from audit_log at verdict-write time so the weekly report
-- can build the offending-agent leaderboard without a JOIN (survives audit_log archiving).

ALTER TABLE auditor_verdicts
    ADD COLUMN IF NOT EXISTS source_agent TEXT NOT NULL DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS idx_auditor_verdicts_source_agent
    ON auditor_verdicts (source_agent);
