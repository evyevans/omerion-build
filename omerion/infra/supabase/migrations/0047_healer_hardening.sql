-- 0047_healer_hardening.sql
-- Adds before_content to audit_log (enables deterministic revert without
-- relying on the _backups/ local filesystem path, which is invisible to
-- Railway containers).
-- Adds healer_recent_fixes view (AUDITOR + HEALER loop detection).

ALTER TABLE audit_log
    ADD COLUMN IF NOT EXISTS before_content TEXT;  -- raw file content before patch

-- View: count consecutive healer fixes per agent in a rolling 1-hour window.
-- HEALER queries this before diagnosing to detect an active loop.
CREATE OR REPLACE VIEW healer_recent_fixes AS
SELECT
    failing_agent,
    COUNT(*)                                        AS fix_count,
    MAX(created_at)                                 AS last_fix_at,
    BOOL_OR(fix_applied)                            AS any_fix_applied
FROM healer_actions
WHERE created_at >= NOW() - INTERVAL '1 hour'
GROUP BY failing_agent;

NOTIFY pgrst, 'reload schema';
