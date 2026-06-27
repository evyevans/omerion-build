-- ════════════════════════════════════════════════════════════════════
-- 0001 — Extensions & Enums
-- Foundation types & extensions for Omerion internal agent OS.
-- ════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "postgis";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ─── Personas (client-facing segmentation) ─────────────────────────
CREATE TYPE persona AS ENUM (
    'team_lead',
    'investor',
    'property_manager',
    'wholesaler',
    'brokerage',
    'solo_agent'
);

-- ─── Account lifecycle ─────────────────────────────────────────────
CREATE TYPE account_status AS ENUM (
    'new',
    'existing',
    'changed',
    'disqualified',
    'client',
    'churned'
);

CREATE TYPE account_tier AS ENUM ('tier_1', 'tier_2', 'tier_3');

-- ─── Contact / opportunity stages ──────────────────────────────────
CREATE TYPE contact_status AS ENUM (
    'new',
    'enriched',
    'scored',
    'engaged',
    'qualified',
    'do_not_contact',
    'opted_out'
);

CREATE TYPE opportunity_stage AS ENUM (
    'new_lead',
    'contacted',
    'engaged',
    'discovery_booked',
    'discovery_done',
    'proposal_sent',
    'won',
    'lost',
    'paused'
);

-- ─── Score segments ────────────────────────────────────────────────
CREATE TYPE score_segment AS ENUM ('hot', 'warm', 'watchlist', 'cold');

-- ─── Build pipeline ────────────────────────────────────────────────
CREATE TYPE build_task_status AS ENUM (
    'pending',
    'in_progress',
    'review',
    'merged',
    'blocked',
    'cancelled'
);

CREATE TYPE deployment_status AS ENUM (
    'queued',
    'deploying',
    'live',
    'rolled_back',
    'failed'
);

-- ─── HITL review ───────────────────────────────────────────────────
CREATE TYPE hitl_decision AS ENUM (
    'pending',
    'approved',
    'rejected',
    'expired',
    'escalated'
);

-- ─── Outreach channels ─────────────────────────────────────────────
CREATE TYPE outreach_channel AS ENUM ('email', 'sms', 'linkedin_dm', 'linkedin_connection', 'voice');

CREATE TYPE outreach_direction AS ENUM ('outbound', 'inbound');

-- ─── R&D lifecycle ─────────────────────────────────────────────────
CREATE TYPE rd_proposal_status AS ENUM (
    'draft',
    'submitted',
    'approved',
    'rejected',
    'in_build',
    'shipped',
    'retired'
);
