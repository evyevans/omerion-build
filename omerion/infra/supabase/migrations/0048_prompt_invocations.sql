-- Migration 0048 — prompt_invocations (Wave 5 v2.1: TRAINER attribution).
--
-- The causal-attribution backbone that turns aggregate "agent X failed 12%
-- of the time" into "prompt CONSTANT_NAME with sha256=abc123 failed on
-- these 10 specific inputs." Without this table, TRAINER reasons on noise.
--
-- One row per ClaudeRouter.complete() call. Written best-effort by the
-- wrapper — a failed insert here NEVER blocks the original LLM call.
--
-- Storage notes:
--   * rendered_input_text / response_text are capped at 8 KB each. Above
--     that we store the head + tail with a marker. Shadow eval needs the
--     text to replay; raw size beyond 8 KB is not informative.
--   * inputs_redacted=true marks rows where the wrapper stripped PII
--     before logging (future: hook into `omerion_core.optout` for
--     contact-level redaction). Default false.
--   * No FK CASCADE on run_id — invocation evidence outlives the run
--     row's lifecycle so TRAINER can replay even after a run is purged.
--
-- Idempotency: invocation_id is the PK (gen_random_uuid). No UNIQUE on
-- (run_id, node_name) because LangGraph can call the LLM multiple times
-- per node (e.g., extraction + reflection).

BEGIN;

CREATE TABLE IF NOT EXISTS prompt_invocations (
    invocation_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Provenance (the missing link to the run lifecycle).
    run_id                UUID         REFERENCES agent_runs(run_id) ON DELETE SET NULL,
    correlation_id        UUID,
    agent_name            TEXT         NOT NULL,
    node_name             TEXT         NOT NULL DEFAULT 'llm_call',

    -- The actual prompt identity — this is what TRAINER attributes failures to.
    prompt_constant_name  TEXT,                 -- e.g. 'NURTURE_SYSTEM'; NULL = ad-hoc call
    prompt_sha256         TEXT,                 -- sha256 of the prompt text at call time
    model                 TEXT         NOT NULL, -- 'claude-sonnet-4-5-…', etc.
    tier                  TEXT,                 -- 'HAIKU' | 'DEFAULT' | 'HEAVY'

    -- Rendered I/O — capped at 8 KB each. Shadow eval replays from these.
    rendered_input_hash   TEXT,                 -- sha256 of the *rendered* user message
    rendered_input_text   TEXT,                 -- ≤ 8 KB; head+tail+marker beyond
    response_text         TEXT,                 -- ≤ 8 KB
    inputs_redacted       BOOLEAN      NOT NULL DEFAULT FALSE,

    -- Cost / latency / outcome
    tokens_in             INTEGER      NOT NULL DEFAULT 0,
    tokens_out            INTEGER      NOT NULL DEFAULT 0,
    cache_read_tokens     INTEGER      NOT NULL DEFAULT 0,
    cache_write_tokens    INTEGER      NOT NULL DEFAULT 0,
    cost_usd              NUMERIC(10,6) NOT NULL DEFAULT 0,
    latency_ms            INTEGER,

    -- Did the *agent node* succeed downstream of this call?
    -- (Not "did the API call return 200" — we care whether the LLM output
    -- was actually usable. The wrapper sets this after node post-validation.)
    success               BOOLEAN,              -- NULL during the call, set on completion
    error_class           TEXT,                 -- 'StyleViolation' | 'ValidationFailed' | …
    error_message         TEXT,                 -- ≤ 2 KB

    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- TRAINER hot-path query: "for agent X and prompt CONSTANT_NAME, what
-- happened in the last 7 days?" — covered by this index.
CREATE INDEX IF NOT EXISTS prompt_invocations_attribution_idx
    ON prompt_invocations (agent_name, prompt_constant_name, success, created_at DESC)
    WHERE prompt_constant_name IS NOT NULL;

-- Run-level lookup for the dashboard timeline (one chain → all invocations).
CREATE INDEX IF NOT EXISTS prompt_invocations_run_idx
    ON prompt_invocations (run_id, created_at)
    WHERE run_id IS NOT NULL;

-- Correlation-chain lookup for cross-agent timelines.
CREATE INDEX IF NOT EXISTS prompt_invocations_correlation_idx
    ON prompt_invocations (correlation_id, created_at)
    WHERE correlation_id IS NOT NULL;

-- Cost-monitoring + R4-style regression alerting reads this.
CREATE INDEX IF NOT EXISTS prompt_invocations_cost_time_idx
    ON prompt_invocations (created_at DESC, agent_name)
    WHERE cost_usd > 0;

COMMENT ON TABLE prompt_invocations IS
    'Wave 5 v2.1: per-LLM-call attribution. One row per ClaudeRouter.complete() invocation. Foundation for TRAINER shadow evaluation, failure clustering, and prompt-causal cost analysis. Best-effort writes — failed inserts never block the LLM call.';

COMMENT ON COLUMN prompt_invocations.prompt_constant_name IS
    'Name of the Python constant from <agent>/prompts.py (e.g. "NURTURE_SYSTEM"). NULL for ad-hoc one-off calls that do not use a named prompt.';

COMMENT ON COLUMN prompt_invocations.prompt_sha256 IS
    'Hash of the system prompt text at call time. TRAINER groups by this to compare prompt variants over time and detect drift between proposal-time snapshot and apply-time reality.';

COMMENT ON COLUMN prompt_invocations.success IS
    'Whether the AGENT NODE succeeded downstream of this call, not whether the HTTP call returned 200. Set by the wrapper after post-validation. NULL means the run is still in flight or the wrapper crashed before recording.';

COMMIT;
