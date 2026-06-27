-- ════════════════════════════════════════════════════════════════════
-- 0006 — Helper functions: emit_event, updated_at triggers, locks
-- ════════════════════════════════════════════════════════════════════

-- ─── emit_event — canonical entry point for agents to publish events ─
CREATE OR REPLACE FUNCTION emit_event(
    p_type         TEXT,
    p_source_agent TEXT,
    p_payload      JSONB DEFAULT '{}'::jsonb,
    p_contact_id   UUID DEFAULT NULL,
    p_account_id   UUID DEFAULT NULL,
    p_correlation_id UUID DEFAULT NULL
) RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
    v_event_id UUID;
BEGIN
    INSERT INTO events (type, source_agent, payload, contact_id, account_id, correlation_id)
    VALUES (p_type, p_source_agent, p_payload, p_contact_id, p_account_id,
            COALESCE(p_correlation_id, uuid_generate_v4()))
    RETURNING event_id INTO v_event_id;
    RETURN v_event_id;
END
$$;

-- ─── updated_at trigger helper ─────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END
$$;

-- Apply trigger to every table with updated_at
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'updated_at'
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_%I_updated_at ON %I; '
            'CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION set_updated_at();',
            tbl, tbl, tbl, tbl
        );
    END LOOP;
END
$$;

-- ─── pg_advisory_lock helpers (idempotent per-target locking) ──────
-- Used by CRM Nurture / Lead Scraper to guarantee only one session
-- works on a given lead / account at a time.
CREATE OR REPLACE FUNCTION try_lock_contact(p_contact_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN pg_try_advisory_lock(hashtextextended('contact:' || p_contact_id::text, 0));
END
$$;

CREATE OR REPLACE FUNCTION release_lock_contact(p_contact_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_unlock(hashtextextended('contact:' || p_contact_id::text, 0));
END
$$;

CREATE OR REPLACE FUNCTION try_lock_account(p_account_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN pg_try_advisory_lock(hashtextextended('account:' || p_account_id::text, 0));
END
$$;

CREATE OR REPLACE FUNCTION release_lock_account(p_account_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_unlock(hashtextextended('account:' || p_account_id::text, 0));
END
$$;

-- ─── HITL token generator — used by omerion_core.hitl ──────────────
CREATE OR REPLACE FUNCTION generate_hitl_tokens()
RETURNS TABLE(approve_token TEXT, reject_token TEXT)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY SELECT
        encode(gen_random_bytes(24), 'base64'),
        encode(gen_random_bytes(24), 'base64');
END
$$;

-- ─── Expire stale HITL reviews (called by R4 cron) ─────────────────
CREATE OR REPLACE FUNCTION expire_stale_hitl_reviews()
RETURNS INT
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INT;
BEGIN
    UPDATE founder_review_queue
       SET decision = 'expired'
     WHERE decision = 'pending'
       AND expires_at < now();
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END
$$;
