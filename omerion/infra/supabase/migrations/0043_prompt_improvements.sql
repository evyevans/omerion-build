-- Migration 0043 — prompt_improvements (Wave 5: TRAINER agent proposals).
--
-- TRAINER runs weekly, identifies underperforming agent prompts, asks an
-- LLM to propose rewrites, and persists every proposal here. The founder
-- approves/rejects via HITL. Per Wave 5 design (user choice 2026-05-24):
-- approved proposals stay at status='approved' in this table — the
-- founder MANUALLY edits prompts.py later using the proposed_text as
-- the source. TRAINER never auto-applies. No GitHub API. No file mutations.
--
-- Status lifecycle:
--   pending      → review row exists in founder_review_queue
--   approved     → founder said yes; awaiting manual prompts.py edit
--   rejected     → founder said no; decision_reason captured
--   applied      → human flipped this manually after editing prompts.py
--                  (so the dashboard can show "X proposals merged this month")
--   superseded   → a newer proposal for the same (agent, constant) week
--                  exists; this one is no longer the active candidate
--   stale        → 30 days passed without a decision; sweeper archives
--
-- Idempotency: UNIQUE(idempotency_key) where the key encodes
--   (agent_name, prompt_constant_name, iso_week). A TRAINER restart
--   inside the same week can't spam the founder with duplicates.
--
-- Safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS prompt_improvements (
    improvement_id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Provenance
    run_id                  UUID         REFERENCES agent_runs(run_id) ON DELETE SET NULL,
    correlation_id          UUID,
    proposed_by             TEXT         NOT NULL DEFAULT 'trainer',
    iso_week                TEXT         NOT NULL,   -- e.g., '2026-W21'

    -- Subject — which prompt is being rewritten
    target_agent_name       TEXT         NOT NULL,
    prompt_constant_name    TEXT         NOT NULL,
    current_text_sha256     TEXT         NOT NULL,   -- drift detection
    current_text            TEXT         NOT NULL,   -- snapshot at proposal time

    -- The proposal itself
    proposed_text           TEXT         NOT NULL,
    rationale               TEXT         NOT NULL CHECK (length(rationale) >= 50),
    expected_impact         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    confidence              NUMERIC(3,2) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),

    -- HITL lifecycle
    status                  TEXT         NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'applied', 'superseded', 'stale')),
    review_id               UUID,
    founder_notes           TEXT,
    decided_at              TIMESTAMPTZ,
    applied_at              TIMESTAMPTZ,
    applied_by              TEXT,

    -- Dedupe + audit
    idempotency_key         TEXT,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- One proposal per (agent, prompt_constant, iso_week). Restart safety.
CREATE UNIQUE INDEX IF NOT EXISTS prompt_improvements_idempotency_uidx
    ON prompt_improvements (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Sweeper + dashboard hot paths.
CREATE INDEX IF NOT EXISTS prompt_improvements_status_idx
    ON prompt_improvements (status, created_at DESC);

CREATE INDEX IF NOT EXISTS prompt_improvements_target_idx
    ON prompt_improvements (target_agent_name, prompt_constant_name, created_at DESC);

CREATE INDEX IF NOT EXISTS prompt_improvements_iso_week_idx
    ON prompt_improvements (iso_week, target_agent_name);

COMMENT ON TABLE prompt_improvements IS
    'Wave 5: TRAINER agent proposals. Per user choice 2026-05-24, approved proposals do NOT auto-merge — founder manually edits prompts.py and flips status to applied. No file mutations from TRAINER, ever.';

COMMENT ON COLUMN prompt_improvements.rationale IS
    'TRAINER guardrail: required, min 50 chars. The "why this improves performance" justification (TWAT spec §A.2).';

COMMENT ON COLUMN prompt_improvements.current_text_sha256 IS
    'Drift detection: if prompts.py changes between proposal-time and apply-time, the diff has shifted under the founder. The applier should hash the current text and compare.';

COMMIT;
