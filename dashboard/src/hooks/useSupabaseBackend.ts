import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { AGENT_DEFS, computeHandoffs, createInitialAgentStates } from '../data/agents';
import type { ActivityEvent, DashboardState, MetricsData, AgentState } from '../types';

export function useSupabaseBackend(): DashboardState {
  const [state, setState] = useState<DashboardState>(() => {
    // Start with all agents idle/clean state
    const cleanAgents: Record<string, AgentState> = {};
    AGENT_DEFS.forEach(def => {
      cleanAgents[def.id] = {
        id: def.id,
        status: 'idle',
        currentTask: null,
        confidenceScore: 0.9,
        lastRunAt: null,
        runsToday: 0,
        successRate: 1.0,
        avgLatencyMs: 0,
        totalCostUsd: 0,
        tokensIn: 0,
        tokensOut: 0,
        lastError: null,
        upstreamAgent: null,
        downstreamAgent: null,
      };
    });

    return {
      agents: cleanAgents,
      activity: [],
      agentMessages: [],
      errorLog: [],
      services: [
        { name: 'Supabase', status: 'connected', latencyMs: 15 },
        { name: 'Claude API', status: 'connected', latencyMs: 300 },
      ],
      handoffs: [],
      approvals: [],
      metrics: {
        totalAgents: AGENT_DEFS.length,
        activeNow: 0,
        hitlPending: 0,
        errors: 0,
        currentClient: 'Omerion Internal',
        systemConfidence: 0.9,
        tokenSpendToday: 0,
        lastAction: null,
      },
    };
  });

  // Sync state if AGENT_DEFS changes (e.g., during Vite hot reload)
  useEffect(() => {
    if (Object.keys(state.agents).length !== AGENT_DEFS.length) {
      setState(prev => {
        const nextAgents = { ...prev.agents };
        AGENT_DEFS.forEach(def => {
          if (!nextAgents[def.id]) {
            nextAgents[def.id] = {
              id: def.id, status: 'idle', currentTask: null,
              confidenceScore: 0.9, lastRunAt: null, runsToday: 0,
              successRate: 1.0, avgLatencyMs: 0, totalCostUsd: 0,
              tokensIn: 0, tokensOut: 0, lastError: null,
              upstreamAgent: null, downstreamAgent: null,
            };
          }
        });
        return {
          ...prev,
          agents: nextAgents,
          metrics: { ...prev.metrics, totalAgents: AGENT_DEFS.length }
        };
      });
    }
  }, [state.agents]);

  useEffect(() => {
    // 1. Initial Fetch
    async function fetchInitialData() {
      // Fetch last 100 runs
      const { data: runs, error } = await supabase
        .from('agent_runs')
        .select('*')
        .order('started_at', { ascending: false })
        .limit(100);

      if (!error && runs) {
        processSupabaseRuns(runs);
      }
    }

    fetchInitialData();

    // 2. Setup Realtime Subscription
    const subscription = supabase
      .channel('public:agent_runs')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'agent_runs' }, payload => {
        processSupabaseRuns([payload.new]);
      })
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'agent_runs' }, payload => {
        processSupabaseRuns([payload.new]);
      })
      .subscribe();

    return () => {
      supabase.removeChannel(subscription);
    };
  }, []);

  // Helper to process raw DB rows into our frontend state
  function processSupabaseRuns(runs: any[]) {
    if (!runs || runs.length === 0) return;

    setState(prev => {
      const nextAgents = { ...prev.agents };
      const newActivity: ActivityEvent[] = [];
      let totalCost = prev.metrics.tokenSpendToday;

      // Ensure runs are processed oldest to newest so state settles correctly
      const sortedRuns = [...runs].sort((a, b) => new Date(a.started_at).getTime() - new Date(b.started_at).getTime());

      for (const run of sortedRuns) {
        // Match on agent id (kebab-case registry key), NOT the display codename.
        // agent_runs.agent_name stores the registry key e.g. "lead-scraper".
        // AGENT_DEFS.name is the display codename e.g. "ENRICH" — never match.
        const agentDef = AGENT_DEFS.find(a => a.id === run.agent_name);
        if (!agentDef) continue;

        const id = agentDef.id;
        const currentAgent = { ...nextAgents[id] };

        // Derive boolean flags from agent_runs.status (no separate success/needs_hitl columns).
        const isFinished = !!run.finished_at;
        const isError = run.status === 'failed' || run.status === 'cancelled';
        const isHitl = run.status === 'hitl_waiting';

        // Correct DB column names: llm_cost_usd, prompt_tokens, completion_tokens.
        // latency_ms is not persisted — compute from timestamps when both are present.
        const costUsd = Number(run.llm_cost_usd ?? run.cost_usd ?? 0);
        const tokensIn = Number(run.prompt_tokens ?? 0);
        const tokensOut = Number(run.completion_tokens ?? 0);
        const latencyMs = (run.started_at && run.finished_at)
          ? new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
          : 0;

        // Update Agent State
        if (!isFinished) {
          currentAgent.status = 'active';
          currentAgent.currentTask = `Running ${agentDef.fullName || run.agent_name}…`;
        } else if (isError) {
          currentAgent.status = 'error';
          currentAgent.currentTask = null;
        } else if (isHitl) {
          currentAgent.status = 'hitl_pending';
          currentAgent.currentTask = null;
        } else {
          // Synthetic visual hold: sub-second clean-success runs land with
          // finished_at almost equal to started_at, so without this branch the
          // agent tile snaps to 'idle' before the user can see it light up.
          // Hold in 'active' for the remainder of a 2500ms window, then a
          // one-shot setTimeout demotes to 'idle'.
          const HOLD_MS = 2500;
          const finishedTs = run.finished_at ? new Date(run.finished_at).getTime() : Date.now();
          const age = Date.now() - finishedTs;
          if (age < HOLD_MS) {
            currentAgent.status = 'active';
            currentAgent.currentTask = `${agentDef.fullName || run.agent_name} just completed…`;
            currentAgent.holdUntil = finishedTs + HOLD_MS;
            const remaining = Math.max(0, HOLD_MS - age);
            setTimeout(() => {
              setState(prev => {
                const cur = prev.agents[id];
                if (!cur) return prev;
                // Only demote if no newer activity has pushed holdUntil forward
                // and the tile is still in the hold state.
                if (cur.status !== 'active') return prev;
                if (cur.holdUntil && Date.now() < cur.holdUntil) return prev;
                return {
                  ...prev,
                  agents: {
                    ...prev.agents,
                    [id]: { ...cur, status: 'idle', currentTask: null, holdUntil: undefined },
                  },
                };
              });
            }, remaining);
          } else {
            currentAgent.status = 'idle';
            currentAgent.currentTask = null;
            currentAgent.holdUntil = undefined;
          }
        }

        currentAgent.runsToday = (currentAgent.runsToday || 0) + 1;
        currentAgent.lastRunAt = new Date(run.started_at || run.created_at);
        currentAgent.totalCostUsd += costUsd;
        currentAgent.tokensIn += tokensIn;
        currentAgent.tokensOut += tokensOut;

        if (latencyMs > 0) {
          currentAgent.avgLatencyMs = currentAgent.avgLatencyMs === 0
            ? latencyMs
            : currentAgent.avgLatencyMs * 0.9 + latencyMs * 0.1;
        }

        if (isError && run.error) {
          currentAgent.lastError = run.error;
        }

        totalCost += costUsd;
        nextAgents[id] = currentAgent;

        // Generate Activity Event for the Timeline (only when finished).
        if (isFinished) {
          newActivity.push({
            id: run.run_id,
            agentId: id,
            agentName: agentDef.name,
            timestamp: new Date(run.finished_at),
            type: isError ? 'error' : (isHitl ? 'hitl' : 'run_complete'),
            message: isError
              ? `Error: ${run.error || 'unknown'}`
              : (run.result_summary || `${agentDef.fullName || run.agent_name} completed`),
            durationMs: latencyMs || undefined,
            costUsd: costUsd,
            confidence: currentAgent.confidenceScore,
            tokenCost: tokensIn + tokensOut,
          });
        }
      }

      const allEvents = [...newActivity, ...prev.activity];
      const seen = new Set();
      const mergedActivity = allEvents
        .filter(e => {
            if (seen.has(e.id)) return false;
            seen.add(e.id);
            return true;
        })
        .sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime())
        .slice(0, 1000);

      // Compute metrics
      const activeNow = Object.values(nextAgents).filter(a => a.status === 'active' || a.status === 'handoff').length;
      const hitlPending = Object.values(nextAgents).filter(a => a.status === 'hitl_pending').length;
      const errors = Object.values(nextAgents).filter(a => a.status === 'error').length;
      
      const lastAction = mergedActivity.length > 0 ? {
          agentName: mergedActivity[0].agentName,
          action: mergedActivity[0].message,
          timestamp: mergedActivity[0].timestamp
      } : null;

      const nextMetrics: MetricsData = {
          ...prev.metrics,
          activeNow,
          hitlPending,
          errors,
          tokenSpendToday: totalCost,
          lastAction,
      };

      return {
          ...prev,
          agents: nextAgents,
          activity: mergedActivity,
          metrics: nextMetrics,
          handoffs: computeHandoffs(nextAgents) // Recompute handoffs if downstream relationships were parsed
      };
    });
  }

  return state;
}
