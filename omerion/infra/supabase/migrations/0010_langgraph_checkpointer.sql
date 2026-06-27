-- ════════════════════════════════════════════════════════════════════
-- 0010 — LangGraph PostgresSaver tables
--
-- Schema is owned by the `langgraph-checkpoint-postgres` library.
-- `PostgresSaver.setup()` is invoked at app boot from
-- `omerion_core.runtime.checkpointer.get_checkpointer()` and creates/
-- upgrades these tables idempotently. We re-declare them here for
-- auditability, Supabase RLS wiring, and disaster-recovery rebuilds
-- without booting the app.
--
-- Tables (as of langgraph-checkpoint-postgres 2.0+):
--   checkpoints, checkpoint_writes, checkpoint_blobs, checkpoint_migrations
--
-- If the library's migration layer advances past what's declared here,
-- `PostgresSaver.setup()` applies the delta on the next boot — so this
-- file does NOT need to be hand-edited on library upgrades.
-- ════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id             TEXT NOT NULL,
    checkpoint_ns         TEXT NOT NULL DEFAULT '',
    checkpoint_id         TEXT NOT NULL,
    parent_checkpoint_id  TEXT,
    type                  TEXT,
    checkpoint            JSONB NOT NULL,
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread
    ON checkpoints (thread_id, checkpoint_ns);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id      TEXT NOT NULL,
    checkpoint_ns  TEXT NOT NULL DEFAULT '',
    checkpoint_id  TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    idx            INT NOT NULL,
    channel        TEXT NOT NULL,
    type           TEXT,
    blob           BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id      TEXT NOT NULL,
    checkpoint_ns  TEXT NOT NULL DEFAULT '',
    channel        TEXT NOT NULL,
    version        TEXT NOT NULL,
    type           TEXT NOT NULL,
    blob           BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

-- RLS: the runtime uses the Supabase DATABASE_URL (direct connection)
-- with superuser-equivalent privileges, so RLS is intentionally OFF on
-- these tables. Do not expose them via the Supabase REST layer.
