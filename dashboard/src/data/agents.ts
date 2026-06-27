/* ══════════════════════════════════════════════════════════════════
   OMERION — Agent Definitions & Mock Data Layer
   21 live backend agents.
   ══════════════════════════════════════════════════════════════════ */

import type {
  AgentDef, AgentState, Department, AgentStatus,
  ActivityEvent, HandoffLink, Approval, MetricsData,
  SystemService, DashboardState,
} from '../types';

// The dashboard runs live against Supabase via hooks/useSupabaseBackend.
// Mock helpers below are retained only for offline preview / Storybook-style
// fixtures; they are not imported by App.tsx anymore.

// ── Department Metadata ──────────────────────────────────────────

export const DEPARTMENTS: Record<Department, { label: string; color: string; shortLabel: string }> = {
  agentic_factory:           { label: 'Agentic Factory',           color: '#D92525', shortLabel: 'FACTORY' },
  lead_gen:                  { label: 'Lead Gen',                  color: '#4A9EFF', shortLabel: 'LEADGEN' },
  research_intelligence:     { label: 'Research & Intelligence',   color: '#9B59B6', shortLabel: 'R&I' },
  client_delivery:           { label: 'Client Delivery',           color: '#00C896', shortLabel: 'DELIVERY' },
  recursive_self_improvement:{ label: 'Recursive Self-Improvement',color: '#B8860B', shortLabel: 'RSI' },
};

// ── 21 Live Agent Definitions ─────────────────────────────────────

export const AGENT_DEFS: AgentDef[] = [
  // ── LEAD GEN (8) ──
  { id: 'market-mapper',      name: 'MAPPER',      fullName: 'Market Territory Mapper',      description: 'Classifies companies into 9-persona taxonomy.', department: 'lead_gen', seatIndex: 0 },
  { id: 'hq-lead-scraping',   name: 'HQ SCOUT',    fullName: 'High Quality Lead Scraper',    description: 'Deep research dossiers per priority account.', department: 'lead_gen', seatIndex: 1 },
  { id: 'lead-scraper',       name: 'ENRICH',      fullName: 'Contact Enrichment Specialist',description: 'Contact discovery + persona enrichment.', department: 'lead_gen', seatIndex: 2 },
  { id: 'icp-scoring',        name: 'ICP SCORER',  fullName: 'Ideal Customer Profile Scorer',description: 'Fit × Intent × Timing scoring.', department: 'lead_gen', seatIndex: 3 },
  { id: 'linkedin-outreach',  name: 'OUTREACH',    fullName: 'Multi-Channel Outreach Agent', description: 'LinkedIn outreach with RAG-augmented drafts.', department: 'lead_gen', seatIndex: 4 },
  { id: 'crm-nurture',        name: 'NURTURE',     fullName: 'Nurture & Drip Sequence Agent',description: 'Email + SMS nurture sequences.', department: 'lead_gen', seatIndex: 5 },
  { id: 'offer-matching',     name: 'PROPOSER',    fullName: 'Proposal & Offer Matching Agent',description: 'Pairs hot contacts to offer packages.', department: 'lead_gen', seatIndex: 6 },
  { id: 'biz-dev-outreach',   name: 'BIZ DEV',     fullName: 'Business Development Outreach',description: 'Finds consulting clients via Contra/Upwork/etc.', department: 'lead_gen', seatIndex: 7 },

  // ── RESEARCH & INTELLIGENCE (3) ──
  { id: 'market-watcher',     name: 'SENTINEL',    fullName: 'Market & Tech Watcher',        description: 'RSS to tagged R&D insights.', department: 'research_intelligence', seatIndex: 8 },
  { id: 'oss-scout',          name: 'SEEKER',      fullName: 'OSS Scout',                    description: 'OSS releases + integration eval.', department: 'research_intelligence', seatIndex: 9 },
  { id: 'strategic-arch',     name: 'STRATEGIST',  fullName: 'Strategic Architecture Designer',description: 'R&D proposal synthesis.', department: 'research_intelligence', seatIndex: 10 },

  // ── CLIENT DELIVERY (3) ──
  { id: 'meeting-intel',       name: 'SCRIBE',      fullName: 'Meeting Intelligence & Documentation',description: 'Transcripts to W5H and blueprint.', department: 'client_delivery', seatIndex: 11 },
  { id: 'build-orchestrator',  name: 'ORCHESTRATOR', fullName: 'Build Orchestrator', description: 'Blueprint deployment and task tracking.', department: 'client_delivery', seatIndex: 12 },
  { id: 'client-onboarding',   name: 'ONBOARD',     fullName: 'Client Onboarding Specialist',  description: 'Automated onboarding flows for new clients.', department: 'client_delivery', seatIndex: 13 },

  // ── RECURSIVE SELF-IMPROVEMENT (4) ──
  { id: 'outcome-attribution', name: 'ATTRIBUTION', fullName: 'Outcome Attribution & ROI Tracker',description: 'Maps closed wins back to agent actions.', department: 'recursive_self_improvement', seatIndex: 14 },
  { id: 'healer-agent',        name: 'HEALER',      fullName: 'Automatic Remediation',        description: 'Fixes rate limits and config errors.', department: 'recursive_self_improvement', seatIndex: 15 },
  { id: 'auditor-agent',       name: 'AUDITOR',     fullName: 'Safety & Compliance Guard',    description: 'Ensures agents do not violate core logic.', department: 'recursive_self_improvement', seatIndex: 16 },
  { id: 'trainer-agent',       name: 'TRAINER',     fullName: 'Prompt Optimization Agent',    description: 'A/B tests and improves agent prompts.', department: 'recursive_self_improvement', seatIndex: 17 },

  // ── AGENTIC FACTORY (3) ──
  { id: 'builder-agent',      name: 'BUILDER',      fullName: 'Headless Code Execution',      description: 'Writes, tests, and submits code.', department: 'agentic_factory', seatIndex: 18 },
  { id: 'validator-agent',    name: 'VALIDATOR',    fullName: 'Quality Assurance Agent',      description: 'Verifies acceptance criteria before merge.', department: 'agentic_factory', seatIndex: 19 },
  { id: 'deployer-agent',     name: 'DEPLOYER',     fullName: 'Deployment & Provisioning',    description: 'Cloud provisioning and health checks.', department: 'agentic_factory', seatIndex: 20 },
];

export function getDeptColor(dept: Department): string {
  return DEPARTMENTS[dept]?.color ?? '#6b6459';
}

export function getDeptLabel(dept: Department): string {
  return DEPARTMENTS[dept]?.label ?? dept;
}

// ── Mock Task Pools ──────────────────────────────────────────────

const TASK_POOL: Record<string, string[]> = {
  'build-orchestrator':  ['Compiling deployment config for client', 'Pushing PR to GitHub', 'Provisioning Supabase schema', 'Running CI/CD pipeline'],
  'builder-agent':       ['Generating 120 lines of code for Auth service', 'Writing tests for payment webhook', 'Refactoring user schema'],
  'validator-agent':     ['Verifying acceptance criteria on PR #29', 'Checking lint and test coverage', 'Commenting on failure lines'],
  'deployer-agent':      ['Running database migrations on Railway', 'Taking point-in-time backup', 'Verifying live health check'],
  'hq-lead-scraping':    ['Scraping verified leads from LinkedIn', 'Running quality filter on 40 contacts', 'Validating email deliverability', 'Pushing 12 leads to enrichment'],
  'lead-scraper':        ['Enriching 14 contact records', 'Deduplicating pipeline entries', 'Fetching LinkedIn firmographic data', 'Resolving email addresses via Hunter.io'],
  'icp-scoring':         ['Scoring 8 new leads — Fit × Intent × Timing', 'Updating persona weight matrix', 'Computing engagement velocity score', 'Running ICP model v2.4'],
  'linkedin-outreach':   ['Composing LinkedIn connection request', 'Scheduling follow-up sequence step 3', 'Checking reply status on 12 threads', 'Generating personalized message draft'],
  'crm-nurture':         ['Sending email to 5 warm leads', 'Loading nurture candidates for re-engagement', 'Drafting SMS sequence step 2', 'Running cooldown check on 23 contacts'],
  'biz-dev-outreach':    ['Searching Upwork for consulting gigs', 'Drafting Contra proposal', 'Evaluating freelance platform leads', 'Composing intro message'],
  'market-mapper':       ['Scanning target markets for accounts', 'Mapping competitive landscape', 'Identifying high-potential territories', 'Updating territory scoring model'],
  'market-watcher':      ['Scanning AI/SaaS industry news feeds', 'Evaluating competitor product launch', 'Drafting market intelligence brief', 'Monitoring 8 RSS sources'],
  'oss-scout':           ['Reviewing 5 GitHub releases', 'Evaluating LangGraph v0.4 integration', 'Writing capability assessment report', 'Checking emerging framework landscape'],
  'strategic-arch':      ['Synthesizing Q2 market trends', 'Building strategic capability brief', 'Analyzing pipeline conversion health', 'Generating architecture recommendation'],
  'offer-matching':      ['Matching prospect to service offerings', 'Generating personalized proposal draft', 'Building 30/60/90 playbook', 'Computing pricing options for enterprise'],
  'meeting-intel':       ['Transcribing 42-min discovery call', 'Extracting 7 action items from meeting', 'Generating structured follow-up doc', 'Pushing commitments to CRM'],
  'client-onboarding':   ['Provisioning client workspace', 'Generating onboarding playbook', 'Sending welcome email sequence'],
  'outcome-attribution': ['Mapping 5 closed deals to AI actions', 'Computing per-agent ROI for Q2', 'Generating attribution report', 'Updating conversion pipeline metrics'],
  'healer-agent':        ['Parsing container error logs', 'Updating config key `agents.crm_nurture.max_retries`', 'Submitting config patch for founder approval'],
  'auditor-agent':       ['Scanning 150 audit records', 'Checking for rule 5 violations (secrets)', 'Executing auto-revert on suspicious patch'],
  'trainer-agent':       ['Analyzing 42 failure clusters', 'Rewriting PROPOSER prompt for edge cases', 'Checking load-bearing clauses against tests'],
};

// ── Mock State Generator ─────────────────────────────────────────

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

const STATUS_WEIGHTS: AgentStatus[] = [
  'active', 'active', 'active', 'active',
  'idle', 'idle', 'idle',
  'waiting', 'handoff', 'hitl_pending',
];

function randomStatus(): AgentStatus {
  return STATUS_WEIGHTS[Math.floor(Math.random() * STATUS_WEIGHTS.length)];
}

export function createInitialAgentStates(): Record<string, AgentState> {
  const states: Record<string, AgentState> = {};
  const agentIds = AGENT_DEFS.map(d => d.id);

  AGENT_DEFS.forEach((def, _i) => {
    const status = randomStatus();
    const runsToday = 8 + Math.floor(Math.random() * 40);
    const isActive = status === 'active' || status === 'handoff';

    // Assign random upstream/downstream for handoff agents
    let upstream: string | null = null;
    let downstream: string | null = null;
    if (status === 'handoff' || status === 'active') {
      const others = agentIds.filter(id => id !== def.id);
      if (Math.random() > 0.5) upstream = others[Math.floor(Math.random() * others.length)];
      if (Math.random() > 0.5) downstream = others[Math.floor(Math.random() * others.length)];
    }

    states[def.id] = {
      id: def.id,
      status,
      currentTask: isActive ? (TASK_POOL[def.id]?.[Math.floor(Math.random() * (TASK_POOL[def.id]?.length ?? 1))] ?? null) : null,
      confidenceScore: 0.72 + Math.random() * 0.24, // 0.72–0.96
      lastRunAt: new Date(Date.now() - Math.random() * 3_600_000),
      runsToday,
      successRate: 0.88 + Math.random() * 0.12,
      avgLatencyMs: 800 + Math.random() * 4200,
      totalCostUsd: runsToday * (0.002 + Math.random() * 0.008),
      tokensIn: runsToday * (1200 + Math.floor(Math.random() * 3000)),
      tokensOut: runsToday * (300 + Math.floor(Math.random() * 800)),
      lastError: null,
      upstreamAgent: upstream,
      downstreamAgent: downstream,
    };
  });

  // Force specific states for demo realism
  if (states['linkedin-outreach']) {
    states['linkedin-outreach'].status = 'hitl_pending';
    states['linkedin-outreach'].currentTask = 'Awaiting human approval on outreach batch #47';
    states['linkedin-outreach'].confidenceScore = 0.91;
  }

  // Force 2-4 handoff pairs (matching real event bus wiring)
  const handoffPairs: [string, string][] = [
    ['lead-scraper', 'icp-scoring'],
    ['market-watcher', 'strategic-arch'],
    ['meeting-intel', 'offer-matching'],
  ];
  handoffPairs.forEach(([from, to]) => {
    if (states[from]) states[from].status = 'handoff';
    if (states[from]) states[from].downstreamAgent = to;
    if (states[to]) states[to].upstreamAgent = from;
  });

  return states;
}

// ── Handoff Links ────────────────────────────────────────────────

export function computeHandoffs(states: Record<string, AgentState>): HandoffLink[] {
  const links: HandoffLink[] = [];
  for (const state of Object.values(states)) {
    if (state.downstreamAgent && (state.status === 'handoff' || state.status === 'active')) {
      const fromDef = AGENT_DEFS.find(d => d.id === state.id);
      const color = fromDef ? getDeptColor(fromDef.department) : '#6b6459';
      links.push({
        fromAgentId: state.id,
        toAgentId: state.downstreamAgent,
        departmentColor: color,
        active: true,
      });
    }
  }
  return links;
}

// ── Mock Approvals ───────────────────────────────────────────────

export function createMockApprovals(): Approval[] {
  return [
    {
      id: uid(),
      agentId: 'linkedin-outreach',
      agentName: 'OUTREACH',
      taskDescription: 'Approve outreach batch #47 — 12 LinkedIn connection requests for enterprise leads scored 85+',
      confidence: 0.91,
      payload: { batchSize: 12, channel: 'linkedin', avgScore: 87.3 },
      createdAt: new Date(Date.now() - 180_000),
    },
  ];
}

// ── Mock Activity Log (30 days) ──────────────────────────────────

export function createMockActivityLog(days: number = 30): ActivityEvent[] {
  const events: ActivityEvent[] = [];
  const now = Date.now();
  const msPerDay = 86_400_000;

  for (let day = 0; day < days; day++) {
    const eventsPerDay = 15 + Math.floor(Math.random() * 25);
    for (let e = 0; e < eventsPerDay; e++) {
      const def = AGENT_DEFS[Math.floor(Math.random() * AGENT_DEFS.length)];
      const timestamp = new Date(now - day * msPerDay - Math.random() * msPerDay);
      const types: ActivityEvent['type'][] = ['run_complete', 'run_complete', 'run_complete', 'run_start', 'handoff', 'info'];
      const type = types[Math.floor(Math.random() * types.length)];
      const tasks = TASK_POOL[def.id] ?? ['Processing task'];
      const task = tasks[Math.floor(Math.random() * tasks.length)];

      events.push({
        id: uid(),
        agentId: def.id,
        agentName: def.name,
        timestamp,
        type,
        message: task,
        durationMs: type === 'run_complete' ? 800 + Math.random() * 5000 : undefined,
        costUsd: type === 'run_complete' ? 0.001 + Math.random() * 0.015 : undefined,
        confidence: 0.72 + Math.random() * 0.24,
        latencyMs: 200 + Math.random() * 3000,
        tokenCost: Math.floor(500 + Math.random() * 4000),
      });
    }
  }

  return events.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime());
}

// ── Mock Metrics ─────────────────────────────────────────────────

export function computeMetrics(states: Record<string, AgentState>, activity: ActivityEvent[]): MetricsData {
  const values = Object.values(states);
  const activeNow = values.filter(s => s.status === 'active' || s.status === 'handoff').length;
  const hitlPending = values.filter(s => s.status === 'hitl_pending').length;
  const errors = values.filter(s => s.status === 'error').length;
  const avgConf = values.reduce((sum, s) => sum + s.confidenceScore, 0) / values.length;
  const totalCost = values.reduce((sum, s) => sum + s.totalCostUsd, 0);
  const lastEvent = activity[0] ?? null;

  return {
    totalAgents: AGENT_DEFS.length,
    activeNow,
    hitlPending,
    errors,
    currentClient: 'Omerion Internal',
    systemConfidence: avgConf,
    tokenSpendToday: totalCost,
    lastAction: lastEvent ? {
      agentName: lastEvent.agentName,
      action: lastEvent.message,
      timestamp: lastEvent.timestamp,
    } : null,
  };
}

// ── Task Cycling ─────────────────────────────────────────────────

export function getNextTask(agentId: string, current: string | null): string | null {
  const pool = TASK_POOL[agentId];
  if (!pool) return null;
  const idx = current ? pool.indexOf(current) : -1;
  return pool[(idx + 1) % pool.length];
}

// ── Initial Services ─────────────────────────────────────────────

export const INITIAL_SERVICES: SystemService[] = [
  { name: 'Supabase',         status: 'connected', latencyMs: 12 },
  { name: 'Claude API',       status: 'connected', latencyMs: 340 },
  { name: 'Pinecone',         status: 'connected', latencyMs: 45 },
  { name: 'Discord',          status: 'connected', latencyMs: 28 },
  { name: 'GitHub',           status: 'connected', latencyMs: 89 },
  { name: 'Google Workspace', status: 'connected', latencyMs: 67 },
  { name: 'Langfuse',         status: 'connected', latencyMs: 34 },
];

// Legacy compat
export const CATEGORY_LABELS: Record<string, string> = {
  revenue: 'Growth',
  intelligence: 'Intel',
  infrastructure: 'Core',
};
