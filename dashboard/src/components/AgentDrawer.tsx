/* ══════════════════════════════════════════════════════════════════
   AgentDrawer — Presidential intelligence panel (Dark Glass)
   ══════════════════════════════════════════════════════════════════ */

import { useState } from 'react';
import type { AgentDef, AgentState, ActivityEvent, Approval } from '../types';
import { getDeptColor, getDeptLabel, AGENT_DEFS } from '../data/agents';

interface Props {
  def: AgentDef;
  agentState: AgentState;
  activity: ActivityEvent[];
  approvals: Approval[];
  onClose: () => void;
  onApproval: (approvalId: string, decision: 'approved' | 'rejected') => void;
}

type LogRange = '7d' | '14d' | '30d';

function timeAgo(date: Date): string {
  const sec = Math.floor((Date.now() - date.getTime()) / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export function AgentDrawer({ def, agentState, activity, approvals, onClose, onApproval }: Props) {
  const [logRange, setLogRange] = useState<LogRange>('7d');
  const deptColor = getDeptColor(def.department);
  const isHitl = agentState.status === 'hitl_pending';

  const now = Date.now();
  const rangeDays = logRange === '7d' ? 7 : logRange === '14d' ? 14 : 30;
  const filteredActivity = activity.filter(e => (now - e.timestamp.getTime()) < rangeDays * 86_400_000);

  const upstreamDef = agentState.upstreamAgent ? (AGENT_DEFS.find(d => d.id === agentState.upstreamAgent) ?? null) : null;
  const downstreamDef = agentState.downstreamAgent ? (AGENT_DEFS.find(d => d.id === agentState.downstreamAgent) ?? null) : null;

  return (
    <>
      {/* Backdrop — Elegant, Dark Sanctum Mask */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0,
          background: 'rgba(5, 4, 3, 0.65)',
          backdropFilter: 'blur(8px) saturate(1.2)',
          WebkitBackdropFilter: 'blur(8px) saturate(1.2)',
          zIndex: 40,
          transition: 'all 0.3s ease',
        }}
      />

      {/* Drawer — Dark Spatial Obsidian Glass Sidebar */}
      <aside
        className="slide-in-right drawer-panel"
        style={{
          position: 'fixed', top: 0, right: 0, bottom: 0,
          width: 400, maxWidth: '100vw',
          zIndex: 50,
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          background: 'rgba(12, 10, 6, 0.88)',
          backdropFilter: 'blur(45px) saturate(1.5) brightness(1.05)',
          WebkitBackdropFilter: 'blur(45px) saturate(1.5) brightness(1.05)',
          borderLeft: '1px solid rgba(220, 200, 155, 0.22)',
          boxShadow: '-12px 0 64px rgba(0, 0, 0, 0.85), inset 1px 0 0 rgba(255,255,255,0.03)',
          transition: 'transform 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        <button onClick={onClose} style={{
          position: 'absolute', top: 16, right: 16, zIndex: 10,
          background: 'rgba(220, 200, 155, 0.08)',
          border: '1px solid rgba(220, 200, 155, 0.22)',
          borderRadius: '50%',
          width: 32, height: 32, display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'pointer', color: '#E8D5A0', fontSize: 13,
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          transition: 'all 0.2s',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = 'rgba(220, 200, 155, 0.18)';
          e.currentTarget.style.borderColor = 'rgba(220, 200, 155, 0.4)';
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'rgba(220, 200, 155, 0.08)';
          e.currentTarget.style.borderColor = 'rgba(220, 200, 155, 0.22)';
        }}
        >
          ✕
        </button>

        {/* ── Header ── */}
        <div style={{
          padding: '44px 32px 24px',
          borderBottom: '1px solid rgba(220, 200, 155, 0.12)',
          display: 'flex', flexDirection: 'column', alignItems: 'center',
          flexShrink: 0
        }}>
          
          {/* Status indicator medallion (Onyx + Brass) */}
          <div style={{
            width: 80, height: 80, borderRadius: '50%',
            background: 'radial-gradient(circle, #251D14 0%, #0F0B06 100%)',
            border: `2px solid rgba(220, 200, 155, 0.35)`,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: `0 12px 32px rgba(0,0,0,0.65), 0 0 16px ${deptColor}44`,
            marginBottom: 16,
            position: 'relative',
          }}>
            {/* Small glowing status ring */}
            <div style={{
              position: 'absolute', inset: -4, borderRadius: '50%',
              border: `1.5px solid ${deptColor}`,
              opacity: 0.6,
              filter: 'blur(2px)',
            }} />
            <span style={{ fontSize: 32, fontWeight: 600, color: '#E8D5A0', fontFamily: 'var(--font-sans)', textShadow: '0 2px 4px rgba(0,0,0,0.5)' }}>
              {def.name.charAt(0)}
            </span>
          </div>

          <h2 className="sans" style={{ fontSize: 22, fontWeight: 700, color: '#F5F0E8', letterSpacing: '0.05em', marginBottom: 4, textShadow: '0 2px 4px rgba(0,0,0,0.6)' }}>
            {def.name}
          </h2>
          <div className="sans" style={{ fontSize: 10, fontWeight: 700, color: deptColor, textTransform: 'uppercase', letterSpacing: '0.15em', marginBottom: 24, textShadow: `0 0 8px ${deptColor}44` }}>
            {getDeptLabel(def.department)}
          </div>

          <div style={{ display: 'flex', gap: 16, width: '100%' }}>
            {/* Status Panel */}
            <div style={{
              flex: 1,
              background: 'rgba(20, 17, 12, 0.55)',
              border: '1px solid rgba(220, 200, 155, 0.12)',
              boxShadow: 'inset 0 1px 2px rgba(255,255,255,0.02), 0 4px 12px rgba(0,0,0,0.3)',
              borderRadius: 12,
              padding: '12px 8px',
              display: 'flex', flexDirection: 'column', alignItems: 'center'
            }}>
              <span className="sans" style={{ fontSize: 8.5, fontWeight: 700, color: 'rgba(230, 215, 185, 0.5)', textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 8 }}>Status</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <span className={`status-orb ${agentState.status}`} style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: agentState.status === 'active' ? 'var(--green)' : agentState.status === 'hitl_pending' ? 'var(--amber)' : agentState.status === 'error' ? 'var(--red)' : '#8E8E93',
                  boxShadow: agentState.status === 'active' ? '0 0 8px var(--green)' : agentState.status === 'hitl_pending' ? '0 0 8px var(--amber)' : agentState.status === 'error' ? '0 0 8px var(--red)' : 'none',
                }} />
                <span className="sans" style={{ fontSize: 12, fontWeight: 600, color: '#F5F0E8', textTransform: 'capitalize' }}>
                  {agentState.status.replace('_', ' ')}
                </span>
              </div>
            </div>

            {/* Confidence Panel */}
            <div style={{
              flex: 1,
              background: 'rgba(20, 17, 12, 0.55)',
              border: '1px solid rgba(220, 200, 155, 0.12)',
              boxShadow: 'inset 0 1px 2px rgba(255,255,255,0.02), 0 4px 12px rgba(0,0,0,0.3)',
              borderRadius: 12,
              padding: '12px 8px',
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              position: 'relative'
            }}>
              <span className="sans" style={{ fontSize: 8.5, fontWeight: 700, color: 'rgba(230, 215, 185, 0.5)', textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 4, zIndex: 2 }}>Confidence</span>
              <svg width="60" height="30" viewBox="0 0 60 30" style={{ overflow: 'visible', marginTop: 10 }}>
                <path d="M 5 30 A 25 25 0 0 1 55 30" fill="none" stroke="rgba(255,255,255,0.04)" strokeWidth="4.5" strokeLinecap="round" />
                <path d="M 5 30 A 25 25 0 0 1 55 30" fill="none" stroke={deptColor} strokeWidth="4.5" strokeLinecap="round"
                  strokeDasharray="78.5" strokeDashoffset={78.5 - (78.5 * agentState.confidenceScore)}
                  style={{ filter: `drop-shadow(0 0 2px ${deptColor}88)` }}
                />
              </svg>
              <div className="sans" style={{ position: 'absolute', bottom: 8, fontSize: 13, fontWeight: 700, color: '#F5F0E8' }}>
                {(agentState.confidenceScore * 100).toFixed(0)}%
              </div>
            </div>
          </div>

          {agentState.currentTask && (
            <div className="sans" style={{
              width: '100%', marginTop: 18, fontSize: 11.5,
              color: 'rgba(235, 225, 205, 0.85)', lineHeight: 1.5,
              padding: '12px 16px',
              background: 'rgba(15, 12, 8, 0.6)',
              border: '1px solid rgba(220, 200, 155, 0.12)',
              borderRadius: 8,
              boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.4)',
              textAlign: 'left'
            }}>
              <span style={{ color: 'rgba(220, 200, 155, 0.5)', textTransform: 'uppercase', fontSize: 8, fontWeight: 700, display: 'block', marginBottom: 4, letterSpacing: '0.08em' }}>Current Task</span>
              {agentState.currentTask}
            </div>
          )}
        </div>

        {/* ── HITL Approval ── */}
        {isHitl && approvals.length > 0 && (
          <div style={{
            margin: '16px 24px 0',
            padding: '16px',
            background: 'rgba(255, 149, 0, 0.07)',
            border: '1px solid rgba(255, 149, 0, 0.35)',
            borderRadius: 12,
            flexShrink: 0,
            boxShadow: '0 8px 24px rgba(0,0,0,0.3), 0 0 16px rgba(255, 149, 0, 0.15)',
          }}>
            <div className="sans" style={{ fontSize: 10, fontWeight: 700, color: '#FFB84D', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 12, textShadow: '0 0 6px rgba(255, 149, 0, 0.4)' }}>
              ⚠ Action Required
            </div>
            {approvals.map(approval => (
              <div key={approval.id}>
                <div className="sans" style={{ fontSize: 12, color: '#F5F0E8', lineHeight: 1.5, marginBottom: 14 }}>
                  {approval.taskDescription}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={() => onApproval(approval.id, 'approved')} style={{
                    flex: 1, padding: '9px',
                    background: 'linear-gradient(180deg, #E8D5A0 0%, #C4A75E 100%)',
                    color: '#0A0805', border: 'none', borderRadius: 6,
                    fontWeight: 700, fontSize: 12, cursor: 'pointer',
                    boxShadow: '0 2px 8px rgba(196,167,94,0.3)',
                    transition: 'all 0.2s',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.filter = 'brightness(1.1)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.filter = 'none'; }}
                  >
                    Approve
                  </button>
                  <button onClick={() => onApproval(approval.id, 'rejected')} style={{
                    flex: 1, padding: '9px',
                    background: 'rgba(255, 59, 48, 0.1)',
                    color: '#FF6B6B', border: '1px solid rgba(255, 59, 48, 0.4)', borderRadius: 6,
                    fontWeight: 700, fontSize: 12, cursor: 'pointer',
                    transition: 'all 0.2s',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255, 59, 48, 0.2)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'rgba(255, 59, 48, 0.1)'; }}
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ── Handoff Chain ── */}
        {(upstreamDef || downstreamDef) && (
          <div style={{ padding: '20px 24px', borderBottom: '1px solid rgba(220, 200, 155, 0.12)', flexShrink: 0 }}>
            <div className="sans" style={{ fontSize: 8.5, fontWeight: 700, color: 'rgba(220, 200, 155, 0.5)', letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 12 }}>
              Handoff Chain
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
              <MiniNode def={upstreamDef} />
              <div style={{ flex: 1, height: 1, background: 'rgba(220, 200, 155, 0.15)' }} />
              <MiniNode def={def} isCurrent />
              <div style={{ flex: 1, height: 1, background: 'rgba(220, 200, 155, 0.15)' }} />
              <MiniNode def={downstreamDef} />
            </div>
          </div>
        )}

        {/* ── Log Header ── */}
        <div style={{ padding: '20px 24px 10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0 }}>
          <span className="sans" style={{ fontSize: 12, fontWeight: 700, color: '#E8D5A0', letterSpacing: '0.05em' }}>
            Action Log
          </span>
          <div style={{ display: 'flex', background: 'rgba(20, 17, 12, 0.65)', borderRadius: 6, padding: 2, border: '1px solid rgba(220, 200, 155, 0.15)' }}>
            {(['7d', '14d', '30d'] as LogRange[]).map(r => (
              <button
                key={r}
                onClick={() => setLogRange(r)}
                className="sans"
                style={{
                  padding: '4px 10px', borderRadius: 4,
                  background: logRange === r ? 'rgba(220, 200, 155, 0.18)' : 'transparent',
                  color: logRange === r ? '#E8D5A0' : 'rgba(220, 200, 155, 0.45)',
                  fontSize: 9.5, fontWeight: 700, cursor: 'pointer', transition: 'all 0.2s',
                  border: logRange === r ? '1px solid rgba(220, 200, 155, 0.3)' : '1px solid transparent',
                }}
              >
                {r.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        {/* ── Activity Log ── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 24px 24px' }}>
          {filteredActivity.length === 0 && (
            <div className="sans" style={{ padding: 32, textAlign: 'center', fontSize: 12, color: 'rgba(220, 200, 155, 0.4)' }}>
              No recent activity
            </div>
          )}
          {filteredActivity.slice(0, 50).map(event => {
            const isError = event.type === 'error';
            const typeColor = isError ? 'var(--red)' : event.type === 'warning' ? 'var(--amber)'
              : event.type === 'run_complete' ? 'var(--green)' : event.type === 'handoff' ? '#E8D5A0' : 'rgba(220, 200, 155, 0.5)';
            
            return (
              <div key={event.id} style={{ padding: '12px 0', borderBottom: '1px solid rgba(220, 200, 155, 0.08)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{
                      width: 5, height: 5, borderRadius: '50%', background: typeColor, display: 'inline-block',
                      boxShadow: isError || event.type === 'warning' || event.type === 'run_complete' ? `0 0 6px ${typeColor}` : 'none'
                    }} />
                    <span className="sans" style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', color: typeColor, letterSpacing: '0.08em' }}>
                      {event.type.replace('_', ' ')}
                    </span>
                  </div>
                  <span className="sans" style={{ fontSize: 10, color: 'rgba(220, 200, 155, 0.4)' }}>
                    {timeAgo(event.timestamp)}
                  </span>
                </div>
                <div className="sans" style={{ fontSize: 11.5, color: 'rgba(235, 225, 205, 0.85)', lineHeight: 1.45 }}>
                  {event.message}
                </div>
              </div>
            );
          })}
        </div>
      </aside>
    </>
  );
}

function MiniNode({ def, isCurrent }: { def: AgentDef | null, isCurrent?: boolean }) {
  if (!def) {
    return (
      <div style={{
        width: 70, height: 26,
        border: '1px dashed rgba(220, 200, 155, 0.2)',
        borderRadius: 6,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(20, 17, 12, 0.25)'
      }}>
        <span className="sans" style={{ fontSize: 9, color: 'rgba(220, 200, 155, 0.4)' }}>None</span>
      </div>
    );
  }
  const color = getDeptColor(def.department);
  
  return (
    <div style={{
      padding: '4px 8px', display: 'flex', alignItems: 'center', gap: 4,
      background: isCurrent ? `${color}22` : 'rgba(20, 17, 12, 0.45)',
      border: `1px solid ${isCurrent ? color : 'rgba(220, 200, 155, 0.15)'}`,
      borderRadius: 6,
      boxShadow: '0 2px 4px rgba(0,0,0,0.2)'
    }}>
      <div style={{
        width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0,
        boxShadow: isCurrent ? `0 0 6px ${color}` : 'none'
      }} />
      <div className="sans" style={{ fontSize: 9, fontWeight: 700, color: isCurrent ? '#FFFFFF' : 'rgba(230, 215, 185, 0.85)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 60 }}>
        {def.name}
      </div>
    </div>
  );
}
