-- 0046_healer_agent.sql
-- Creates audit_log (AUDITOR reads this; HEALER writes it)
-- and healer_actions (HEALER's own run-level tracking).
--
-- Note: healer_actions.run_id stores the LangGraph session_id string — not a
-- hard FK to agent_runs, because the session_id assigned by the broker may
-- differ from the agent_runs.run_id created in the same invocation.

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_agent        TEXT        NOT NULL,
    action_type         TEXT        NOT NULL,    -- 'config_patch' | 'prompt_update' | 'agent_revert'
    target_resource     TEXT        NOT NULL,    -- e.g. 'config/agents.yaml', 'skills/crm-nurture.skill.md'
    diff_summary        TEXT        NOT NULL,    -- human-readable change description (≤ 2000 chars)
    raw_payload         JSONB       NOT NULL DEFAULT '{}',
    hitl_review_id      UUID        REFERENCES founder_review_queue(review_id),
    reverted            BOOLEAN     NOT NULL DEFAULT FALSE,
    requires_git_revert BOOLEAN     NOT NULL DEFAULT FALSE,
    audited             BOOLEAN     NOT NULL DEFAULT FALSE,  -- AUDITOR flips this after sweep
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS audit_log_source_agent_idx ON audit_log (source_agent, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_audited_idx      ON audit_log (audited, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_target_idx       ON audit_log (target_resource);

CREATE TABLE IF NOT EXISTS healer_actions (
    action_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id           TEXT,                        -- LangGraph session_id (no hard FK)
    audit_id         UUID        REFERENCES audit_log(audit_id),
    failing_agent    TEXT        NOT NULL,
    severity         TEXT        NOT NULL,        -- 'low' | 'medium' | 'high' | 'critical'
    metric           TEXT        NOT NULL,        -- e.g. 'error_rate', 'latency_ms', 'cost_usd'
    metric_value     NUMERIC(12,4),
    root_cause       TEXT,
    remediation_type TEXT,                        -- 'config_patch' | 'prompt_update' | 'escalated'
    fix_applied      BOOLEAN     NOT NULL DEFAULT FALSE,
    healing_notes    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS healer_actions_agent_idx ON healer_actions (failing_agent, created_at DESC);
CREATE INDEX IF NOT EXISTS healer_actions_run_idx   ON healer_actions (run_id);

NOTIFY pgrst, 'reload schema';
