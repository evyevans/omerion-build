/* ══════════════════════════════════════════════════════════════════
   OMERION Round Table — Type Definitions
   ══════════════════════════════════════════════════════════════════ */

export type AgentStatus = 'active' | 'idle' | 'waiting' | 'handoff' | 'error' | 'hitl_pending';

export type Department =
  | 'agentic_factory'
  | 'lead_gen'
  | 'research_intelligence'
  | 'client_delivery'
  | 'recursive_self_improvement';

export interface AgentDef {
  id: string;
  name: string;            // ALL CAPS short name
  fullName: string;        // Full persona title
  description: string;
  department: Department;
  seatIndex: number;       // 0-based position around the table
  planned?: boolean;       // true → no backend code yet; render dimmed with PLANNED badge
  plannedReason?: string;  // short hint shown in drawer (e.g. "Phase 5.1")
}

export interface AgentState {
  id: string;
  status: AgentStatus;
  currentTask: string | null;
  confidenceScore: number; // 0.0–1.0
  lastRunAt: Date | null;
  runsToday: number;
  successRate: number;     // 0–1
  avgLatencyMs: number;
  totalCostUsd: number;
  tokensIn: number;
  tokensOut: number;
  lastError: string | null;
  upstreamAgent: string | null;
  downstreamAgent: string | null;
  // Wall-clock ms (epoch) until which the agent should remain visually 'active'
  // after a finished run lands. Lets the dashboard hold sub-second runs on
  // screen long enough for the user's eye to register them.
  holdUntil?: number;
}

export interface ActivityEvent {
  id: string;
  agentId: string;
  agentName: string;
  timestamp: Date;
  type: 'run_complete' | 'run_start' | 'error' | 'info' | 'warning' | 'handoff' | 'hitl';
  message: string;
  durationMs?: number;
  costUsd?: number;
  confidence?: number;
  inputSummary?: string;
  outputSummary?: string;
  latencyMs?: number;
  tokenCost?: number;
}

export interface HandoffLink {
  fromAgentId: string;
  toAgentId: string;
  departmentColor: string;
  active: boolean;
}

export interface Approval {
  id: string;
  agentId: string;
  agentName: string;
  taskDescription: string;
  confidence: number;
  payload: Record<string, unknown>;
  createdAt: Date;
}

export interface SystemService {
  name: string;
  status: 'connected' | 'degraded' | 'disconnected';
  latencyMs?: number;
}

export interface AgentMessage {
  id: string;
  runId: string | null;
  fromAgent: string;
  toAgent: string | null;
  message: string;
  eventType: string | null;
  createdAt: Date;
}

export interface ErrorLogEntry {
  id: string;
  source: string;
  message: string;
  traceback: string | null;
  meta: Record<string, unknown>;
  occurredAt: Date;
}

export interface MetricsData {
  totalAgents: number;
  activeNow: number;
  hitlPending: number;
  errors: number;
  currentClient: string;
  systemConfidence: number;
  tokenSpendToday: number;
  lastAction: {
    agentName: string;
    action: string;
    timestamp: Date;
  } | null;
}

export interface DashboardState {
  agents: Record<string, AgentState>;
  activity: ActivityEvent[];
  agentMessages: AgentMessage[];
  errorLog: ErrorLogEntry[];
  services: SystemService[];
  handoffs: HandoffLink[];
  approvals: Approval[];
  metrics: MetricsData;
}

// Legacy compat
export type AgentCategory = 'revenue' | 'intelligence' | 'infrastructure';
