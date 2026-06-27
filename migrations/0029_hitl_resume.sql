-- 0029_hitl_resume.sql
-- HITL approval + workflow resume infrastructure for the new (108-file) build.
-- Replaces the legacy founder_review_queue (omerion/infra/supabase/migrations/0004)
-- which the api/approvals/router.py was previously trying to patch by a column
-- (approval_id) that does not exist on the legacy table.
--
-- Idempotent. Safe to re-run.

-- ─── HITL decision enum ──────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE hitl_decision_v2 AS ENUM ('pending','approve','reject','edit','expired');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ─── agent_approvals ────────────────────────────────────────────────
-- One row per HITL escalation. approval_id matches ApprovalRequest.approval_id
-- emitted by core/runtime/confidence_engine.build_approval().
CREATE TABLE IF NOT EXISTS agent_approvals (
    approval_id      UUID PRIMARY KEY,
    client_slug      TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    correlation_id   TEXT NOT NULL,
    subject          TEXT NOT NULL,
    context_md       TEXT NOT NULL DEFAULT '',
    draft_ref        JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence       NUMERIC(4,3) NOT NULL DEFAULT 0,
    thread_id        UUID,                                  -- FK shape; links agent_pending_resumes
    resume_kind      TEXT NOT NULL DEFAULT 'single_execute',-- 'single_execute' | 'langgraph'
    decision         hitl_decision_v2 NOT NULL DEFAULT 'pending',
    decided_by       TEXT,
    decided_at       TIMESTAMPTZ,
    notes            TEXT,
    edits            JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '48 hours')
);
CREATE INDEX IF NOT EXISTS idx_agent_approvals_decision
    ON agent_approvals (decision, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_approvals_client
    ON agent_approvals (client_slug, agent_name, created_at DESC);

-- ─── agent_pending_resumes ──────────────────────────────────────────
-- One row per single-execute agent that is paused waiting for a HITL
-- decision. Resume payload is the original input plus the agent output
-- that triggered the gate; on decide() the resumer reads this row,
-- merges the decision into the payload, and re-invokes the agent.
CREATE TABLE IF NOT EXISTS agent_pending_resumes (
    thread_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_id      UUID NOT NULL UNIQUE,
    client_slug      TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    correlation_id   TEXT NOT NULL,
    input_payload    JSONB NOT NULL,         -- the payload the agent was called with
    paused_output    JSONB NOT NULL,         -- AgentOutput.model_dump that triggered the gate
    tenant_ctx       JSONB NOT NULL,         -- minimal TenantContext snapshot for resume
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','resumed','expired','failed')),
    resumed_output   JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resumed_at       TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '48 hours')
);
CREATE INDEX IF NOT EXISTS idx_pending_resumes_status
    ON agent_pending_resumes (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_pending_resumes_approval
    ON agent_pending_resumes (approval_id);

-- ─── Reload PostgREST cache so the API can see the new tables/columns
NOTIFY pgrst, 'reload schema';
