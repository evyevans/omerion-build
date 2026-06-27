-- 0015_research_dossiers_unique_account.sql
-- Phase A C3: enable idempotent dossier writes via upsert(on_conflict="account_id").
-- Today the schema permits multiple dossiers per account; semantically each
-- account has a single living dossier that gets refreshed. Collapse duplicates
-- (keep newest) and add the constraint that lets the upsert work.

WITH ranked AS (
    SELECT dossier_id,
           ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY created_at DESC) AS rn
    FROM research_dossiers
    WHERE account_id IS NOT NULL
)
DELETE FROM research_dossiers
WHERE dossier_id IN (SELECT dossier_id FROM ranked WHERE rn > 1);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'research_dossiers_account_id_unique'
    ) THEN
        ALTER TABLE research_dossiers
            ADD CONSTRAINT research_dossiers_account_id_unique UNIQUE (account_id);
    END IF;
END$$;
