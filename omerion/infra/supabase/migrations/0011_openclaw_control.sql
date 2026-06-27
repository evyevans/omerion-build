-- 0011_openclaw_control.sql
-- Phase 8: OpenClaw messaging control surface (default channel: Telegram).
-- Tracks (a) active chat sessions with the founder / team, and
-- (b) an audit log of every inbound + outbound message that crossed
-- the OpenClaw bridge (approve/reject/edit/triggers/status queries).

CREATE TABLE IF NOT EXISTS openclaw_sessions (
    session_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel          TEXT NOT NULL CHECK (channel IN ('telegram', 'whatsapp', 'imessage', 'signal', 'other')),
    chat_target      TEXT NOT NULL,              -- Telegram chat_id, WA phone, iMessage E.164, etc.
    display_name     TEXT,
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'revoked')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS openclaw_sessions_target_idx
    ON openclaw_sessions (chat_target);


CREATE TABLE IF NOT EXISTS openclaw_audit_log (
    entry_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID REFERENCES openclaw_sessions(session_id) ON DELETE SET NULL,
    direction        TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    skill            TEXT,                -- skill name (approve_hitl, pending_approvals, ...)
    review_id        UUID,                -- when the message ties to a HITL row
    message_text     TEXT NOT NULL,
    response_status  INT,                 -- HTTP status from the control-plane call, when applicable
    correlation_id   UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS openclaw_audit_log_session_idx
    ON openclaw_audit_log (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS openclaw_audit_log_review_idx
    ON openclaw_audit_log (review_id)
    WHERE review_id IS NOT NULL;
