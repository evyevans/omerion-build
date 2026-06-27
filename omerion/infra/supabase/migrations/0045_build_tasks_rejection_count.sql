ALTER TABLE build_tasks
    ADD COLUMN IF NOT EXISTS rejection_count INT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_build_tasks_rejection_count
    ON build_tasks (rejection_count)
    WHERE rejection_count > 0;

NOTIFY pgrst, 'reload schema';
