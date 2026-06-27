-- 0045_builder_task_status.sql
-- BUILDER (Agent #11) uses branch_open, pr_open, and failed.
-- ADD VALUE IF NOT EXISTS is idempotent on PG 9.6+ (Supabase).

ALTER TYPE build_task_status ADD VALUE IF NOT EXISTS 'branch_open';
ALTER TYPE build_task_status ADD VALUE IF NOT EXISTS 'pr_open';
ALTER TYPE build_task_status ADD VALUE IF NOT EXISTS 'failed';
