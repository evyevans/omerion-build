-- 0023_agent_messages.sql
-- Persistent log of agent-to-agent handoff narration and system notifications.
-- Replaces tmp/rq_notified.json and tmp/digest_sent.json with a proper DB table.
-- Feeds: Discord #omerion-room narration, dashboard team-chat panel, HITL dedup.

CREATE TABLE IF NOT EXISTS agent_messages (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      uuid        REFERENCES agent_runs(run_id) ON DELETE SET NULL,
    from_agent  text        NOT NULL,
    to_agent    text,
    message     text        NOT NULL,
    event_type  text,
    meta        jsonb       NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_created_at  ON agent_messages (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_from_agent  ON agent_messages (from_agent);
CREATE INDEX IF NOT EXISTS idx_agent_messages_event_type  ON agent_messages (event_type);
