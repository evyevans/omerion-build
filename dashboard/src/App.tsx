/* ══════════════════════════════════════════════════════════════════
   OMERION — The Round Table  ·  App Root
   ══════════════════════════════════════════════════════════════════ */

import { useCallback, useState } from 'react';
import { AGENT_DEFS } from './data/agents';
import { MetricsBar } from './components/MetricsBar';
import { RoundTable } from './components/RoundTable';
import { AgentDrawer } from './components/AgentDrawer';
import { TimelineView } from './components/TimelineView';
import { SpendCalendar } from './components/SpendCalendar';
import { useSupabaseBackend } from './hooks/useSupabaseBackend';

type ViewTab = 'table' | 'timeline' | 'spend';
type TimeRange = '7d' | '14d' | '30d';

export default function App() {
  const [view, setView] = useState<ViewTab>('table');
  const [timeRange, setTimeRange] = useState<TimeRange>('7d');
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  // Hook into live Supabase backend
  const liveState = useSupabaseBackend();

  // For backward compatibility / local overrides if needed, though handleApproval is stubbed.
  const [localApprovals, setLocalApprovals] = useState(liveState.approvals);

  const selectAgent = useCallback((id: string | null) => setSelectedAgentId(id), []);

  const handleApproval = useCallback((approvalId: string, _decision: 'approved' | 'rejected') => {
    // In a real app, this would hit a Supabase RPC or endpoint to resolve the HITL block.
    setLocalApprovals(prev => prev.filter(a => a.id !== approvalId));
  }, []);

  const state = { ...liveState, approvals: localApprovals };

  const selectedDef = selectedAgentId ? AGENT_DEFS.find(d => d.id === selectedAgentId) ?? null : null;
  const selectedState = selectedAgentId ? state.agents[selectedAgentId] ?? null : null;
  const selectedActivity = selectedAgentId
    ? state.activity.filter(e => e.agentId === selectedAgentId)
    : [];

  const isTableView = view === 'table';

  return (
    <div style={{
      width: '100%', height: '100%', display: 'flex', flexDirection: 'column',
      background: '#0A0805', // Always dark to match all three views
      transition: 'background 0.3s ease',
    }}>
      {/* ── Metrics Bar ── */}
      <MetricsBar metrics={state.metrics} isTableView={isTableView} />

      {/* ── View Tabs ── */}
      <nav
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '0 32px',
          borderBottom: '1px solid rgba(220,200,155,0.12)',
          flexShrink: 0,
          background: 'rgba(10, 8, 4, 0.55)',
          backdropFilter: 'blur(30px) saturate(1.3)',
          WebkitBackdropFilter: 'blur(30px) saturate(1.3)',
          height: 48,
          transition: 'all 0.3s ease',
        }}
      >
        {(['table', 'timeline', 'spend'] as ViewTab[]).map(tab => {
          const isActive = view === tab;
          return (
            <button
              key={tab}
              onClick={() => setView(tab)}
              style={{
                padding: '0 16px',
                height: '100%',
                background: 'transparent',
                border: 'none',
                borderBottom: isActive ? '2px solid #E8D5A0' : '2px solid transparent',
                color: isActive ? '#E8D5A0' : 'rgba(220,200,155,0.4)',
                fontFamily: 'var(--font-sans)',
                fontSize: 13,
                fontWeight: 700,
                letterSpacing: '0.12em',
                textTransform: 'uppercase' as const,
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                textShadow: '0 1px 6px rgba(0,0,0,0.9)',
              }}
            >
              {tab === 'table' ? 'Round Table' : tab === 'timeline' ? 'Timeline' : 'Spend Calendar'}
            </button>
          );
        })}
        {view === 'timeline' && (
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, padding: '4px 0' }}>
            {(['7d', '14d', '30d'] as TimeRange[]).map(range => (
              <button
                key={range}
                onClick={() => setTimeRange(range)}
                className="sans"
                style={{
                  padding: '6px 16px',
                  borderRadius: 100,
                  border: timeRange === range ? '1px solid rgba(220,200,155,0.2)' : '1px solid transparent',
                  background: timeRange === range ? 'rgba(220,200,155,0.1)' : 'transparent',
                  color: timeRange === range ? '#E8D5A0' : 'rgba(220,200,155,0.4)',
                  boxShadow: timeRange === range ? '0 1px 4px rgba(0,0,0,0.5)' : 'none',
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: '0.08em',
                  cursor: 'pointer',
                  transition: 'all 0.2s',
                }}
              >
                {range.toUpperCase()}
              </button>
            ))}
          </div>
        )}
      </nav>

      {/* ── Main View ── */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {view === 'table' ? (
          <RoundTable
            agents={state.agents}
            handoffs={state.handoffs}
            metrics={state.metrics}
            activity={state.activity}
            onSelectAgent={selectAgent}
            selectedAgentId={selectedAgentId}
          />
        ) : view === 'timeline' ? (
          <TimelineView
            activity={state.activity}
            timeRange={timeRange}
            onSelectAgent={selectAgent}
          />
        ) : (
          <SpendCalendar activity={state.activity} />
        )}
      </div>

      {/* ── Agent Drawer ── */}
      {selectedDef && selectedState && (
        <AgentDrawer
          def={selectedDef}
          agentState={selectedState}
          activity={selectedActivity}
          approvals={state.approvals.filter(a => a.agentId === selectedAgentId)}
          onClose={() => selectAgent(null)}
          onApproval={handleApproval}
        />
      )}
    </div>
  );
}
