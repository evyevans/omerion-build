/* ══════════════════════════════════════════════════════════════════
   TimelineView — The Sanctum Temporal Canvas
   Dark brushed-obsidian background, glassmorphic architectural panels,
   gold inner borders, glowing LED conduit activity bars.
   Zero white. Zero flat. This is a command center, not a spreadsheet.
   ══════════════════════════════════════════════════════════════════ */

import { useMemo, useRef, useState } from 'react';
import { AGENT_DEFS, getDeptColor, DEPARTMENTS } from '../data/agents';
import type { ActivityEvent, Department } from '../types';

interface Props {
  activity: ActivityEvent[];
  timeRange: '7d' | '14d' | '30d';
  onSelectAgent: (id: string | null) => void;
}

const DEPT_ORDER: Department[] = [
  'agentic_factory', 'lead_gen', 'research_intelligence',
  'client_delivery', 'recursive_self_improvement',
];

const DEPT_COLORS: Record<Department, string> = {
  agentic_factory: '#D92525',
  lead_gen: '#4A9EFF',
  research_intelligence: '#9B59B6',
  client_delivery: '#00C896',
  recursive_self_improvement: '#B8860B',
};

const TOOLS_CONFIG = [
  { id: 'supabase',   label: 'Supabase',   icon: 'M12 4L4 12l8 8 8-8-8-8zm0 18l-8-8 8 8 8-8-8 8z' },
  { id: 'anthropic',  label: 'Anthropic',  icon: 'M12 4c-4.4 0-8 3.6-8 8s3.6 8 8 8 8-3.6 8-8-3.6-8-8-8zM12 16c-2.2 0-4-1.8-4-4s1.8-4 4-4 4 1.8 4 4-1.8 4-4 4z' },
  { id: 'github',     label: 'GitHub',     icon: 'M12 2C6.48 2 2 6.48 2 12c0 4.42 2.87 8.17 6.84 9.5.5.08.66-.23.66-.5v-1.69c-2.77.6-3.36-1.34-3.36-1.34-.45-1.15-1.11-1.46-1.11-1.46-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.87 1.52 2.34 1.07 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.92 0-1.11.38-2 1.03-2.71-.1-.25-.45-1.29.1-2.64 0 0 .84-.27 2.75 1.02.79-.22 1.65-.33 2.5-.33.85 0 1.71.11 2.5.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.35.2 2.39.1 2.64.65.71 1.03 1.6 1.03 2.71 0 3.82-2.34 4.66-4.57 4.91.36.31.69.92.69 1.85V21c0 .27.16.59.67.5C19.14 20.16 22 16.42 22 12A10 10 0 0012 2z' },
  { id: 'slack',      label: 'Slack',      icon: 'M8 8h8v8H8z' },
  { id: 'hubspot',    label: 'HubSpot',    icon: 'M12 4l8 16H4z' },
  { id: 'exa',        label: 'Exa.ai',     icon: 'M4 4h16v16H4zM6 6v12h12V6z' },
  { id: 'perplexity', label: 'Perplexity', icon: 'M10 4h4v16h-4zM4 10h16v4H4z' },
];

function detectTools(message: string) {
  const msg = message.toLowerCase();
  const found: string[] = [];
  if (msg.includes('supabase') || msg.includes('db') || msg.includes('query')) found.push('supabase');
  if (msg.includes('claude') || msg.includes('anthropic') || msg.includes('llm')) found.push('anthropic');
  if (msg.includes('github') || msg.includes('commit') || msg.includes('code')) found.push('github');
  if (msg.includes('slack') || msg.includes('message') || msg.includes('hitl')) found.push('slack');
  if (msg.includes('hubspot') || msg.includes('crm') || msg.includes('contact')) found.push('hubspot');
  if (msg.includes('exa') || msg.includes('scrape') || msg.includes('search')) found.push('exa');
  if (msg.includes('perplexity') || msg.includes('research')) found.push('perplexity');
  return TOOLS_CONFIG.filter(t => found.includes(t.id));
}

function formatDate(date: Date): string {
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatTime(date: Date): string {
  return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

export function TimelineView({ activity, timeRange, onSelectAgent }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredEvent, setHoveredEvent] = useState<ActivityEvent | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  const days = timeRange === '7d' ? 7 : timeRange === '14d' ? 14 : 30;
  const now = Date.now();
  const rangeStart = now - days * 86_400_000;

  const orderedAgents = useMemo(() => {
    const result: typeof AGENT_DEFS = [];
    DEPT_ORDER.forEach(dept => {
      result.push(...AGENT_DEFS.filter(a => a.department === dept));
    });
    return result;
  }, []);

  const eventsByAgent = useMemo(() => {
    const map: Record<string, ActivityEvent[]> = {};
    orderedAgents.forEach(def => { map[def.id] = []; });
    activity.forEach(e => {
      if (e.timestamp.getTime() >= rangeStart && map[e.agentId]) {
        map[e.agentId].push(e);
      }
    });
    return map;
  }, [activity, rangeStart, orderedAgents]);

  const dateLabels = useMemo(() => {
    const labels: { date: Date; x: number }[] = [];
    const step = days <= 7 ? 1 : days <= 14 ? 2 : 3;
    for (let d = 0; d <= days; d += step) {
      const date = new Date(now - (days - d) * 86_400_000);
      labels.push({ date, x: (d / days) * 100 });
    }
    return labels;
  }, [days, now]);

  const deptBoundaries = useMemo(() => {
    const boundaries: { dept: Department; firstIndex: number }[] = [];
    orderedAgents.forEach((def, idx) => {
      const prev = idx > 0 ? orderedAgents[idx - 1] : null;
      if (!prev || prev.department !== def.department) {
        boundaries.push({ dept: def.department, firstIndex: idx });
      }
    });
    return boundaries;
  }, [orderedAgents]);

  const ROW_HEIGHT = 48;
  const HEADER_HEIGHT = 46;
  const LABEL_WIDTH = 220;
  const totalRange = now - rangeStart;

  // Obsidian brushed-stone texture via CSS noise pattern
  const obsidianBg = `
    radial-gradient(ellipse at 50% 0%, rgba(28,22,12,0.95) 0%, rgba(10,8,4,1) 60%)
  `;

  return (
    <div
      id="timeline-view"
      ref={containerRef}
      style={{
        width: '100%', height: '100%', overflow: 'auto',
        background: '#0A0805',
        backgroundImage: obsidianBg,
        position: 'relative',
        willChange: 'scroll-position',
      }}
    >
      <style>{`
        /* Brushed obsidian noise texture */
        #timeline-view::before {
          content: '';
          position: fixed;
          inset: 0;
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='400'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75 0.2' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='400' height='400' filter='url(%23n)' opacity='0.025'/%3E%3C/svg%3E");
          pointer-events: none;
          z-index: 0;
          mix-blend-mode: overlay;
        }

        /* Glowing LED conduit bar keyframe */
        @keyframes conduit-pulse {
          0%, 100% { opacity: 0.85; }
          50% { opacity: 1; }
        }

        /* Agent row hover state */
        .tl-agent-row:hover .tl-label-cell {
          background: rgba(212,175,55,0.05) !important;
        }
        .tl-agent-row:hover {
          background: rgba(255,255,255,0.018) !important;
        }
      `}</style>

      {/* ════════════════════════════════════════════════════════════ */}
      {/* Sticky date axis — Architectural glass header              */}
      {/* ════════════════════════════════════════════════════════════ */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 10,
        height: HEADER_HEIGHT,
        // Frosted architectural glass panel
        background: 'rgba(12, 9, 4, 0.82)',
        backdropFilter: 'blur(32px) saturate(1.5)',
        WebkitBackdropFilter: 'blur(32px) saturate(1.5)',
        // 1px muted-gold inner border at bottom
        borderBottom: '1px solid rgba(212,175,55,0.22)',
        boxShadow: 'inset 0 -1px 0 rgba(212,175,55,0.1), 0 4px 24px rgba(0,0,0,0.4)',
        display: 'flex', alignItems: 'stretch',
      }}>
        {/* Agent label column glass header */}
        <div style={{
          width: LABEL_WIDTH, flexShrink: 0,
          borderRight: '1px solid rgba(212,175,55,0.2)',
          display: 'flex', alignItems: 'center',
          paddingLeft: 24,
          background: 'rgba(8,6,2,0.3)',
        }}>
          <span style={{
            fontSize: 8, fontWeight: 700, letterSpacing: '0.2em',
            color: 'rgba(212,175,55,0.5)', textTransform: 'uppercase',
            fontFamily: 'var(--font-sans)',
            textShadow: '0 1px 4px rgba(0,0,0,0.8)',
          }}>
            Council Agent
          </span>
        </div>

        {/* Date labels */}
        <div style={{ position: 'relative', flex: 1, display: 'flex', alignItems: 'flex-end', paddingBottom: 8 }}>
          {dateLabels.map(({ date, x }, i) => (
            <div
              key={i}
              style={{
                position: 'absolute', left: `${x}%`,
                transform: 'translateX(-50%)',
                display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2,
              }}
            >
              {/* Gold tick mark */}
              <div style={{
                width: 1, height: 6,
                background: 'rgba(212,175,55,0.4)',
                marginBottom: 2,
              }} />
              <span style={{
                fontSize: 10, fontWeight: 600,
                color: 'rgba(220,200,160,0.75)',
                letterSpacing: '0.06em',
                whiteSpace: 'nowrap',
                fontFamily: 'var(--font-sans)',
                // Text etched with subtle white drop-shadow for legibility
                textShadow: '0 1px 0 rgba(255,255,255,0.1), 0 1px 6px rgba(0,0,0,0.8)',
              }}>
                {formatDate(date)}
              </span>
            </div>
          ))}
          {/* Current moment indicator */}
          <div style={{
            position: 'absolute', right: 0, top: 0, bottom: 0,
            width: 1,
            background: 'linear-gradient(to bottom, rgba(212,175,55,0.6), rgba(212,175,55,0.1))',
          }} />
        </div>
      </div>

      {/* ════════════════════════════════════════════════════════════ */}
      {/* Agent rows — dark stone with alternating micro-opacity      */}
      {/* ════════════════════════════════════════════════════════════ */}
      <div style={{ contain: 'layout style', position: 'relative', zIndex: 1 }}>
        {orderedAgents.map((def, rowIdx) => {
          const deptColor = DEPT_COLORS[def.department] || getDeptColor(def.department);
          const agentEvents = eventsByAgent[def.id] || [];
          const showDeptHeader = deptBoundaries.some(b => b.firstIndex === rowIdx);
          const isEvenRow = rowIdx % 2 === 0;

          return (
            <div key={def.id}>
              {/* ── Department header — gold-bordered glass band ── */}
              {showDeptHeader && (
                <div style={{
                  height: 34,
                  display: 'flex', alignItems: 'center',
                  paddingLeft: 24,
                  marginTop: rowIdx > 0 ? 2 : 0,
                  // Glassmorphic dept header with dept-color tint
                  background: `linear-gradient(90deg, ${deptColor}14 0%, rgba(10,8,4,0.6) 60%, transparent 100%)`,
                  backdropFilter: 'blur(12px)',
                  // Gold inner border at top and bottom
                  borderTop: '1px solid rgba(212,175,55,0.15)',
                  borderBottom: '1px solid rgba(212,175,55,0.1)',
                  boxShadow: `inset 3px 0 0 ${deptColor}66`,
                }}>
                  <span style={{
                    fontSize: 9, fontWeight: 800,
                    letterSpacing: '0.18em',
                    color: deptColor,
                    textTransform: 'uppercase',
                    fontFamily: 'var(--font-sans)',
                    textShadow: `0 0 12px ${deptColor}44, 0 1px 4px rgba(0,0,0,0.9)`,
                  }}>
                    {DEPARTMENTS[def.department]?.label}
                  </span>
                </div>
              )}

              {/* ── Agent row ── */}
              <div
                className="tl-agent-row"
                style={{
                  height: ROW_HEIGHT,
                  display: 'flex', alignItems: 'center',
                  // Alternating micro-opacity dark rows
                  background: isEvenRow
                    ? 'rgba(255,255,255,0.012)'
                    : 'rgba(0,0,0,0.0)',
                  borderBottom: '1px solid rgba(255,255,255,0.03)',
                  cursor: 'pointer',
                  transition: 'background 0.15s ease',
                }}
                onClick={() => onSelectAgent(def.id)}
              >
                {/* Agent label — glass panel cell */}
                <div
                  className="tl-label-cell"
                  style={{
                    width: LABEL_WIDTH, flexShrink: 0,
                    padding: '0 24px',
                    display: 'flex', alignItems: 'center', gap: 10,
                    height: '100%',
                    // Frosted glass left panel
                    background: 'rgba(8,6,2,0.4)',
                    backdropFilter: 'blur(8px)',
                    // Gold right border — defines the glass panel edge
                    borderRight: '1px solid rgba(212,175,55,0.18)',
                    transition: 'background 0.15s ease',
                  }}
                >
                  {/* Dept color LED orb */}
                  <div style={{
                    width: 7, height: 7, borderRadius: '50%',
                    background: deptColor, flexShrink: 0,
                    boxShadow: `0 0 6px ${deptColor}99, 0 0 12px ${deptColor}44`,
                  }} />
                  <span style={{
                    fontSize: 11, fontWeight: 600,
                    color: 'rgba(235,220,195,0.9)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    letterSpacing: '0.06em',
                    fontFamily: 'var(--font-sans)',
                    textShadow: '0 1px 4px rgba(0,0,0,0.8)',
                  }}>
                    {def.name}
                  </span>
                </div>

                {/* Timeline track */}
                <div style={{ flex: 1, position: 'relative', height: '100%', overflow: 'hidden' }}>
                  {/* Vertical date grid lines — gold hairlines */}
                  {dateLabels.map(({ x }, i) => (
                    <div key={i} style={{
                      position: 'absolute', left: `${x}%`,
                      top: 0, bottom: 0, width: 1,
                      background: 'rgba(212,175,55,0.08)',
                    }} />
                  ))}

                  {/* Activity events — glowing LED conduits */}
                  {agentEvents.map(event => {
                    const elapsed = event.timestamp.getTime() - rangeStart;
                    const xPercent = (elapsed / totalRange) * 100;

                    const isEventError = event.type === 'error';
                    const isHitl = event.type === 'hitl';
                    const isHandoff = event.type === 'handoff';

                    let marker: React.ReactNode;

                    if (isEventError) {
                      // Red error beacon
                      marker = (
                        <div style={{
                          width: 8, height: 8, borderRadius: '50%',
                          background: '#FF3B30',
                          boxShadow: '0 0 8px #FF3B30, 0 0 16px rgba(255,59,48,0.4)',
                        }} />
                      );
                    } else if (isHitl) {
                      // Amber HITL beacon
                      marker = (
                        <div style={{
                          width: 8, height: 8, borderRadius: '50%',
                          background: '#FF9500',
                          boxShadow: '0 0 8px #FF9500, 0 0 16px rgba(255,149,0,0.4)',
                        }} />
                      );
                    } else if (isHandoff) {
                      // Small gold diamond handoff marker
                      marker = (
                        <div style={{
                          width: 6, height: 6,
                          background: 'rgba(212,175,55,0.7)',
                          transform: 'rotate(45deg)',
                          boxShadow: '0 0 4px rgba(212,175,55,0.5)',
                        }} />
                      );
                    } else {
                      // Glowing LED conduit bar — physical bar of light
                      // Minimum 24px floor guarantees the LED bar is visible
                      // for sub-second / zero-cost runs that would otherwise
                      // collapse to a 6px sliver and disappear into the rail.
                      const rawW = event.durationMs
                        ? (event.durationMs / totalRange) * 100 * 1000
                        : 0;
                      const barWidth = Math.min(Math.max(rawW, 24), 48);
                      marker = (
                        <div style={{
                          width: barWidth, height: 14,
                          borderRadius: 4,
                          // Physical LED bar: bright core with dept-color glow
                          background: `linear-gradient(to bottom,
                            rgba(255,255,255,0.25) 0%,
                            ${deptColor}EE 20%,
                            ${deptColor}BB 70%,
                            rgba(0,0,0,0.2) 100%
                          )`,
                          boxShadow: `
                            0 0 8px ${deptColor}99,
                            0 0 16px ${deptColor}44,
                            inset 0 1px 0 rgba(255,255,255,0.3)
                          `,
                          animation: 'conduit-pulse 3s ease-in-out infinite',
                        }} />
                      );
                    }

                    return (
                      <div
                        key={event.id}
                        style={{
                          position: 'absolute', left: `${xPercent}%`,
                          top: '50%', transform: 'translate(-50%, -50%)',
                          zIndex: isEventError || isHitl ? 10 : 1,
                        }}
                        onMouseEnter={e => {
                          setHoveredEvent(event);
                          setTooltipPos({ x: e.clientX, y: e.clientY });
                        }}
                        onMouseLeave={() => setHoveredEvent(null)}
                        onClick={e => { e.stopPropagation(); onSelectAgent(def.id); }}
                      >
                        {marker}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* ════════════════════════════════════════════════════════════ */}
      {/* Tooltip — Dark glass, gold borders, etched text            */}
      {/* ════════════════════════════════════════════════════════════ */}
      {hoveredEvent && (
        <div style={{
          position: 'fixed',
          left: tooltipPos.x + 16, top: tooltipPos.y - 60,
          padding: '14px 18px', maxWidth: 320,
          zIndex: 60, pointerEvents: 'none',
          background: 'rgba(10, 8, 4, 0.94)',
          backdropFilter: 'blur(40px) saturate(1.5)',
          WebkitBackdropFilter: 'blur(40px) saturate(1.5)',
          border: '1px solid rgba(212,175,55,0.2)',
          borderTop: '1px solid rgba(212,175,55,0.4)',
          borderRadius: 10,
          boxShadow: '0 20px 60px rgba(0,0,0,0.8), inset 0 1px 0 rgba(212,175,55,0.06)',
        }}>
          <div style={{
            fontSize: 9, fontWeight: 800,
            color: 'rgba(212,175,55,0.8)',
            letterSpacing: '0.18em', marginBottom: 8,
            textTransform: 'uppercase',
            fontFamily: 'var(--font-sans)',
            textShadow: '0 0 8px rgba(212,175,55,0.3)',
          }}>
            {hoveredEvent.agentName}
          </div>
          <div style={{
            fontSize: 13, color: 'rgba(230,215,185,0.85)',
            lineHeight: 1.5, marginBottom: 12,
            fontFamily: 'var(--font-sans)',
            textShadow: '0 1px 4px rgba(0,0,0,0.8)',
          }}>
            {hoveredEvent.message}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', gap: 12 }}>
              <span style={{
                fontSize: 11, fontWeight: 600,
                color: 'rgba(212,175,55,0.5)',
                fontFamily: 'var(--font-sans)',
              }}>
                {formatTime(hoveredEvent.timestamp)}
              </span>
              {hoveredEvent.durationMs && (
                <span style={{
                  fontSize: 11, fontWeight: 600,
                  color: 'rgba(212,175,55,0.5)',
                  fontFamily: 'var(--font-sans)',
                }}>
                  {(hoveredEvent.durationMs / 1000).toFixed(1)}s
                </span>
              )}
              {hoveredEvent.costUsd != null && hoveredEvent.costUsd > 0 && (
                <span style={{
                  fontSize: 11, fontWeight: 600,
                  color: 'rgba(0,200,150,0.7)',
                  fontFamily: 'var(--font-sans)',
                }}>
                  ${hoveredEvent.costUsd.toFixed(4)}
                </span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 5 }}>
              {detectTools(hoveredEvent.message).map(tool => (
                <div key={tool.id} title={tool.label} style={{
                  background: 'rgba(212,175,55,0.08)',
                  border: '0.5px solid rgba(212,175,55,0.2)',
                  borderRadius: 4, padding: '3px 5px',
                  display: 'flex', alignItems: 'center',
                }}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="rgba(212,175,55,0.6)">
                    <path d={tool.icon} />
                  </svg>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
