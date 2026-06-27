-- 0012_discord_channel.sql
-- Extend the openclaw_sessions channel CHECK constraint to include 'discord'
-- (migrated from Telegram as the primary HITL surface).

ALTER TABLE openclaw_sessions
    DROP CONSTRAINT IF EXISTS openclaw_sessions_channel_check;

ALTER TABLE openclaw_sessions
    ADD CONSTRAINT openclaw_sessions_channel_check
    CHECK (channel IN ('discord', 'telegram', 'whatsapp', 'imessage', 'signal', 'other'));
