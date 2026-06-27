# Extended Supabase SQL Mitigations
Run this securely in your Supabase SQL Editor. It covers all extended ecosystem tables for the Omerion agents.

```sql
-- ============================================================
-- OMERION EXTENDED ECOSYSTEM MIGRATION
-- Safe and Idempotent. Will NOT drop existing data.
-- ============================================================

-- ── rd_insights (WATCH Agent) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.rd_insights (
  id bigserial primary key
);
ALTER TABLE public.rd_insights ADD COLUMN IF NOT EXISTS source_url text not null default '';
ALTER TABLE public.rd_insights ADD COLUMN IF NOT EXISTS source text;
ALTER TABLE public.rd_insights ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE public.rd_insights ADD COLUMN IF NOT EXISTS published_at timestamptz;
ALTER TABLE public.rd_insights ADD COLUMN IF NOT EXISTS content text;
ALTER TABLE public.rd_insights ADD COLUMN IF NOT EXISTS metadata jsonb not null default '{}'::jsonb;
ALTER TABLE public.rd_insights ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
ALTER TABLE public.rd_insights ADD COLUMN IF NOT EXISTS updated_at timestamptz not null default now();
CREATE UNIQUE INDEX IF NOT EXISTS rd_insights_source_url_key on public.rd_insights (source_url);

-- ── rd_oss_candidates (SEEK Agent) ────────────────────────────
CREATE TABLE IF NOT EXISTS public.rd_oss_candidates (
  id bigserial primary key
);
ALTER TABLE public.rd_oss_candidates ADD COLUMN IF NOT EXISTS repo_url text not null default '';
ALTER TABLE public.rd_oss_candidates ADD COLUMN IF NOT EXISTS repo_owner text;
ALTER TABLE public.rd_oss_candidates ADD COLUMN IF NOT EXISTS repo_name text;
ALTER TABLE public.rd_oss_candidates ADD COLUMN IF NOT EXISTS score numeric;
ALTER TABLE public.rd_oss_candidates ADD COLUMN IF NOT EXISTS notes text;
ALTER TABLE public.rd_oss_candidates ADD COLUMN IF NOT EXISTS metadata jsonb not null default '{}'::jsonb;
ALTER TABLE public.rd_oss_candidates ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
ALTER TABLE public.rd_oss_candidates ADD COLUMN IF NOT EXISTS updated_at timestamptz not null default now();
CREATE UNIQUE INDEX IF NOT EXISTS rd_oss_candidates_repo_url_key on public.rd_oss_candidates (repo_url);

-- ── rd_proposals (SHAPE Agent) ────────────────────────────────
CREATE TABLE IF NOT EXISTS public.rd_proposals (
  id bigserial primary key
);
ALTER TABLE public.rd_proposals ADD COLUMN IF NOT EXISTS title text not null default '';
ALTER TABLE public.rd_proposals ADD COLUMN IF NOT EXISTS title_hash text not null default '';
ALTER TABLE public.rd_proposals ADD COLUMN IF NOT EXISTS run_date date not null default CURRENT_DATE;
ALTER TABLE public.rd_proposals ADD COLUMN IF NOT EXISTS status text not null default 'draft';
ALTER TABLE public.rd_proposals ADD COLUMN IF NOT EXISTS body text;
ALTER TABLE public.rd_proposals ADD COLUMN IF NOT EXISTS metadata jsonb not null default '{}'::jsonb;
ALTER TABLE public.rd_proposals ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
ALTER TABLE public.rd_proposals ADD COLUMN IF NOT EXISTS updated_at timestamptz not null default now();
CREATE UNIQUE INDEX IF NOT EXISTS rd_proposals_titlehash_rundate_key on public.rd_proposals (title_hash, run_date);

-- ── clients (RUN Agent) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.clients (
  id bigserial primary key
);
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS client_slug text not null default '';
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS name text;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS metadata jsonb not null default '{}'::jsonb;
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS updated_at timestamptz not null default now();
CREATE UNIQUE INDEX IF NOT EXISTS clients_client_slug_key on public.clients (client_slug);

-- ── deployments (RUN Agent) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS public.deployments (
  id bigserial primary key
);
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS deployment_id text not null default '';
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS client_slug text;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS status text not null default 'pending';
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS metadata jsonb not null default '{}'::jsonb;
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
ALTER TABLE public.deployments ADD COLUMN IF NOT EXISTS updated_at timestamptz not null default now();
CREATE UNIQUE INDEX IF NOT EXISTS deployments_deployment_id_key on public.deployments (deployment_id);

-- ── build_tasks (RUN Agent) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS public.build_tasks (
  id bigserial primary key
);
ALTER TABLE public.build_tasks ADD COLUMN IF NOT EXISTS deployment_id text not null default '';
ALTER TABLE public.build_tasks ADD COLUMN IF NOT EXISTS slug text not null default '';
ALTER TABLE public.build_tasks ADD COLUMN IF NOT EXISTS status text not null default 'queued';
ALTER TABLE public.build_tasks ADD COLUMN IF NOT EXISTS metadata jsonb not null default '{}'::jsonb;
ALTER TABLE public.build_tasks ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
ALTER TABLE public.build_tasks ADD COLUMN IF NOT EXISTS updated_at timestamptz not null default now();
CREATE UNIQUE INDEX IF NOT EXISTS build_tasks_deployment_slug_key on public.build_tasks (deployment_id, slug);

-- ── attribution_reports (PROVE Agent) ─────────────────────────
CREATE TABLE IF NOT EXISTS public.attribution_reports (
  id bigserial primary key
);
ALTER TABLE public.attribution_reports ADD COLUMN IF NOT EXISTS deployment_id text not null default '';
ALTER TABLE public.attribution_reports ADD COLUMN IF NOT EXISTS report jsonb not null default '{}'::jsonb;
ALTER TABLE public.attribution_reports ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
ALTER TABLE public.attribution_reports ADD COLUMN IF NOT EXISTS updated_at timestamptz not null default now();
CREATE UNIQUE INDEX IF NOT EXISTS attribution_reports_deployment_key on public.attribution_reports (deployment_id);

-- ── case_study_drafts (PROVE Agent) ───────────────────────────
CREATE TABLE IF NOT EXISTS public.case_study_drafts (
  id bigserial primary key
);
ALTER TABLE public.case_study_drafts ADD COLUMN IF NOT EXISTS deployment_id text not null default '';
ALTER TABLE public.case_study_drafts ADD COLUMN IF NOT EXISTS doc_type text not null default '';
ALTER TABLE public.case_study_drafts ADD COLUMN IF NOT EXISTS doc_id text;
ALTER TABLE public.case_study_drafts ADD COLUMN IF NOT EXISTS content text;
ALTER TABLE public.case_study_drafts ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
ALTER TABLE public.case_study_drafts ADD COLUMN IF NOT EXISTS updated_at timestamptz not null default now();
CREATE UNIQUE INDEX IF NOT EXISTS case_study_drafts_deployment_doctype_key on public.case_study_drafts (deployment_id, doc_type);

-- ── hitl_tasks (GUARD Agent) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS public.hitl_tasks (
  id bigserial primary key
);
ALTER TABLE public.hitl_tasks ADD COLUMN IF NOT EXISTS task_key text NOT NULL DEFAULT '';
ALTER TABLE public.hitl_tasks ADD COLUMN IF NOT EXISTS agent_name text NOT NULL DEFAULT '';
ALTER TABLE public.hitl_tasks ADD COLUMN IF NOT EXISTS field text;
ALTER TABLE public.hitl_tasks ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'open';
ALTER TABLE public.hitl_tasks ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE public.hitl_tasks ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.hitl_tasks ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS hitl_tasks_unresolved_agent_field_key on public.hitl_tasks (agent_name, field) where status = 'open';

-- ── revenue_events (PROVE Agent) ──────────────────────────────
CREATE TABLE IF NOT EXISTS public.revenue_events (
  id bigserial primary key
);
ALTER TABLE public.revenue_events ADD COLUMN IF NOT EXISTS deployment_id text;
ALTER TABLE public.revenue_events ADD COLUMN IF NOT EXISTS client_slug text;
ALTER TABLE public.revenue_events ADD COLUMN IF NOT EXISTS amount numeric;
ALTER TABLE public.revenue_events ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE public.revenue_events ADD COLUMN IF NOT EXISTS event_date date;
ALTER TABLE public.revenue_events ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
CREATE INDEX IF NOT EXISTS revenue_events_deployment_idx ON public.revenue_events (deployment_id);

-- ── lead_conversions (PROVE Agent) ────────────────────────────
CREATE TABLE IF NOT EXISTS public.lead_conversions (
  id bigserial primary key
);
ALTER TABLE public.lead_conversions ADD COLUMN IF NOT EXISTS deployment_id text;
ALTER TABLE public.lead_conversions ADD COLUMN IF NOT EXISTS contact_id uuid;
ALTER TABLE public.lead_conversions ADD COLUMN IF NOT EXISTS conversion_date date;
ALTER TABLE public.lead_conversions ADD COLUMN IF NOT EXISTS value numeric;
ALTER TABLE public.lead_conversions ADD COLUMN IF NOT EXISTS created_at timestamptz not null default now();
CREATE INDEX IF NOT EXISTS lead_conversions_deployment_idx ON public.lead_conversions (deployment_id);

-- ============================================================
-- END OF MIGRATION
-- ============================================================
```
