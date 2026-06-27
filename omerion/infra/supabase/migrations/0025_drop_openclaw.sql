-- ════════════════════════════════════════════════════════════════════
-- 0025 — Drop OpenClaw: tables, data migration, constraint update
-- ════════════════════════════════════════════════════════════════════
-- All OpenClaw functionality is replaced by the first-party Discord
-- bot in discord/omerion_bot.py. This migration is safe to re-run.

-- ── Step 1: Drop OpenClaw-specific tables ─────────────────────────
DROP TABLE IF EXISTS openclaw_sessions  CASCADE;
DROP TABLE IF EXISTS openclaw_audit_log CASCADE;

-- ── Step 2: Migrate historical data ───────────────────────────────
-- Any agent_runs rows that were triggered via OpenClaw have
-- source_channel = 'openclaw'. Reclassify them as 'discord' because
-- OpenClaw was purely a Discord relay — the originating channel was
-- always Discord.
UPDATE public.agent_runs
    SET source_channel = 'discord'
    WHERE source_channel = 'openclaw';

-- Also catch any other unexpected values that would block the
-- constraint — map them to 'api' as a safe fallback.
UPDATE public.agent_runs
    SET source_channel = 'api'
    WHERE source_channel NOT IN ('discord', 'scheduler', 'api', 'event');

-- ── Step 3: Replace source_channel CHECK constraint ───────────────
-- Drop the old constraint (may include 'openclaw' or not exist yet).
ALTER TABLE public.agent_runs
    DROP CONSTRAINT IF EXISTS agent_runs_source_channel_check;

-- Add the new constraint. All rows are now in the allowed set.
ALTER TABLE public.agent_runs
    ADD CONSTRAINT agent_runs_source_channel_check
    CHECK (source_channel IN ('discord', 'scheduler', 'api', 'event'));
