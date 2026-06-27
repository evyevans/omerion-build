/* ══════════════════════════════════════════════════════════════════
   RoundTable — The Grand Parliament Chamber
   Physical obsidian table, carved brass channels, volumetric agent
   drop shadows, spatial frosted glass UI. Zero mock animations.
   All 18 AgentCard components: 100% position/color/label locked.
   ══════════════════════════════════════════════════════════════════ */

import { useMemo, useRef, useEffect, useState, useId } from 'react';
import { AGENT_DEFS, getDeptColor, DEPARTMENTS } from '../data/agents';
import type { AgentState, HandoffLink, MetricsData, ActivityEvent, Department, AgentDef } from '../types';
import { AgentCard } from './AgentCard';
import { GrandHallCanvas } from './GrandHallCanvas';

interface Props {
  agents: Record<string, AgentState>;
  handoffs: HandoffLink[];
  metrics: MetricsData;
  activity: ActivityEvent[];
  onSelectAgent: (id: string | null) => void;
  selectedAgentId: string | null;
}

interface SeatPosition {
  x: number;
  y: number;
  angle: number;
  tableIndex: number;
}

const UNIFIED_TABLE_DEPTS: Department[] = [
  'agentic_factory', 'lead_gen', 'research_intelligence', 'client_delivery', 'recursive_self_improvement'
];

const DEPT_COLORS: Record<Department, string> = {
  agentic_factory: '#D92525',
  lead_gen: '#4A9EFF',
  research_intelligence: '#9B59B6',
  client_delivery: '#00C896',
  recursive_self_improvement: '#B8860B',
};

function getSeatPosition(
  index: number, total: number,
  cx: number, cy: number, rx: number, ry: number,
  tableIndex: number
): SeatPosition {
  const angle = (index / total) * 2 * Math.PI - Math.PI / 2;
  return {
    x: cx + rx * 1.08 * Math.cos(angle),
    y: cy + ry * 1.08 * Math.sin(angle),
    angle,
    tableIndex,
  };
}

/* ══════════════════════════════════════════════════════════════════
   BrassChannelHandoff
   Live-only: when isActive, a warm molten-gold pulse surges
   through the carved brass channel between two agent seats.
   When inactive: channel shows as a faint physical inset groove.
   ══════════════════════════════════════════════════════════════════ */
function BrassChannelHandoff({
  from, to, color, isActive,
}: {
  from: SeatPosition; to: SeatPosition; color: string; isActive: boolean;
}) {
  const midX = (from.x + to.x) / 2;
  const midY = (from.y + to.y) / 2;
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.sqrt(dx * dx + dy * dy);

  // Bezier arcs through table interior, pulled slightly inward
  const perpX = -dy / len;
  const perpY = dx / len;
  const arcH = Math.min(len * 0.22, 80);
  const ctrlX = midX + perpX * arcH;
  const ctrlY = midY + perpY * arcH;

  const pathD = `M ${from.x} ${from.y} Q ${ctrlX} ${ctrlY} ${to.x} ${to.y}`;
  const travelDur = `${(2.8 + (len / 900) * 1.6).toFixed(1)}s`;

  return (
    <g>
      {/* ── Physical channel — always present as carved groove ── */}
      {/* Shadow below channel (3D recession illusion) */}
      <path
        d={pathD}
        fill="none"
        stroke="rgba(0,0,0,0.5)"
        strokeWidth="5"
        strokeLinecap="round"
        style={{ filter: 'blur(1.5px)' }}
        opacity="0.6"
      />
      {/* Channel body — dark recessed groove */}
      <path
        d={pathD}
        fill="none"
        stroke="#0F0C08"
        strokeWidth="3.5"
        strokeLinecap="round"
      />
      {/* Brass lining — catches ambient light from skylight */}
      <path
        d={pathD}
        fill="none"
        stroke={isActive ? `${color}` : 'rgba(180,140,40,0.25)'}
        strokeWidth="1.8"
        strokeLinecap="round"
        style={{
          filter: isActive ? `drop-shadow(0 0 4px ${color}88)` : 'none',
          transition: 'stroke 0.8s ease, filter 0.8s ease',
        }}
      />
      {/* Specular highlight on top edge of brass */}
      <path
        d={pathD}
        fill="none"
        stroke="rgba(255,242,200,0.15)"
        strokeWidth="0.6"
        strokeLinecap="round"
      />

      {/* ── Live molten gold surge — ONLY when isActive (live handoff) ── */}
      {isActive && (
        <>
          {/* Outer warm glow — molten heat bleed */}
          <path
            d={pathD}
            fill="none"
            stroke={color}
            strokeWidth="8"
            strokeLinecap="round"
            opacity="0"
            style={{ filter: `blur(5px)` }}
          >
            <animate
              attributeName="opacity"
              values="0;0.35;0.35;0"
              keyTimes="0;0.1;0.85;1"
              dur={travelDur}
              repeatCount="indefinite"
            />
          </path>

          {/* Core molten surge along the channel */}
          <circle r="5" fill={color}
            style={{
              filter: `drop-shadow(0 0 10px ${color}) drop-shadow(0 0 20px ${color}88) drop-shadow(0 0 35px ${color}44)`,
            }}>
            <animateMotion dur={travelDur} repeatCount="indefinite" path={pathD} />
            <animate attributeName="opacity" values="0;0;1;1;0"
              keyTimes="0;0.04;0.12;0.88;1"
              dur={travelDur} repeatCount="indefinite" />
            <animate attributeName="r" values="5;6;5;6;5"
              dur={`${parseFloat(travelDur) * 0.2}s`} repeatCount="indefinite" />
          </circle>

          {/* Brilliant white core spark */}
          <circle r="2" fill="#FFFFF0">
            <animateMotion dur={travelDur} repeatCount="indefinite" path={pathD} />
            <animate attributeName="opacity" values="0;0;0.95;0.95;0"
              keyTimes="0;0.05;0.14;0.87;1"
              dur={travelDur} repeatCount="indefinite" />
          </circle>
        </>
      )}
    </g>
  );
}

/* ══════════════════════════════════════════════════════════════════
   AgentTableShadow
   Realistic drop shadow cast by each agent onto the table surface.
   Elliptical shadow beneath agent, intensity driven by status.
   ══════════════════════════════════════════════════════════════════ */
function AgentTableShadow({ x, y, isActive }: { x: number; y: number; isActive: boolean }) {
  return (
    <ellipse
      cx={x}
      cy={y + 28}
      rx={34}
      ry={10}
      fill="rgba(0,0,0,0.55)"
      style={{ filter: 'blur(6px)' }}
      opacity={isActive ? 0.7 : 0.45}
    />
  );
}

/* ══════════════════════════════════════════════════════════════════
   ActiveTableReflection
   When an agent is active (task_processing), the table surface in
   front of them shows a localized, breathing illumination patch —
   simulating ambient bounce light from their screen/glow.
   LIVE ONLY — bound to agent.status from backend.
   ══════════════════════════════════════════════════════════════════ */
function ActiveTableReflection({ x, y, color, isActive }: {
  x: number; y: number; color: string; isActive: boolean;
}) {
  if (!isActive) return null;
  return (
    <ellipse
      cx={x}
      cy={y + 14}
      rx={42}
      ry={14}
      fill={color}
      opacity={0}
      style={{
        filter: 'blur(12px)',
        animation: 'breathing-glow 2.8s ease-in-out infinite',
        mixBlendMode: 'screen',
      }}
    />
  );
}

/* ══════════════════════════════════════════════════════════════════
   PhysicalOnyxTable — Polished obsidian/dark walnut table
   Dept arcs = physically carved brass channels in the surface.
   No floating neon. No CSS glow rings.
   ══════════════════════════════════════════════════════════════════ */
function PhysicalOnyxTable({
  cx, cy, rx, ry, innerScale, agents, depts, seats,
}: {
  cx: number; cy: number; rx: number; ry: number;
  innerScale: number;
  agents: AgentDef[]; depts: Department[];
  seats: SeatPosition[];
}) {
  const uid = useId().replace(/:/g, '');
  const irx = rx * innerScale;
  const iry = ry * innerScale;

  const deptArcs = useMemo(() => {
    const arcs: { dept: Department; startAngle: number; endAngle: number; color: string }[] = [];
    let offset = 0;
    depts.forEach(dept => {
      const count = agents.filter(a => a.department === dept).length;
      if (count === 0) return;
      const startAngle = (offset / agents.length) * 2 * Math.PI - Math.PI / 2;
      const endAngle = ((offset + count) / agents.length) * 2 * Math.PI - Math.PI / 2;
      arcs.push({ dept, startAngle, endAngle, color: DEPT_COLORS[dept] });
      offset += count;
    });
    return arcs;
  }, [agents, depts]);

  // Brass channel radius — sits right at table rim surface
  const brassR = rx - 16;
  const brassRY = ry - 10;

  return (
    <svg style={{ position: 'absolute', inset: 0, overflow: 'visible', pointerEvents: 'none' }}>
      <defs>
        {/* ── Polished obsidian surface ── */}
        <radialGradient id={`obsidian-${uid}`} cx="40%" cy="30%" r="80%">
          <stop offset="0%"   stopColor="#232016" />
          <stop offset="25%"  stopColor="#181410" />
          <stop offset="55%"  stopColor="#0F0D09" />
          <stop offset="85%"  stopColor="#090806" />
          <stop offset="100%" stopColor="#060504" />
        </radialGradient>

        {/* ── Skylight specular reflection on table surface ── */}
        <radialGradient id={`skylight-refl-${uid}`} cx="50%" cy="28%" r="55%">
          <stop offset="0%"   stopColor="rgba(255,255,248,0.22)" />
          <stop offset="30%"  stopColor="rgba(245,235,210,0.12)" />
          <stop offset="60%"  stopColor="rgba(220,210,180,0.06)" />
          <stop offset="100%" stopColor="rgba(200,190,160,0)" />
        </radialGradient>

        {/* ── Warm edge bevel — table rim catching skylight ── */}
        <radialGradient id={`rim-catch-${uid}`} cx="50%" cy="20%" r="65%">
          <stop offset="0%"   stopColor="rgba(200,180,130,0.12)" />
          <stop offset="100%" stopColor="rgba(150,130,90,0)" />
        </radialGradient>

        {/* ── Donut mask ── */}
        <mask id={`donut-${uid}`}>
          <ellipse cx={cx} cy={cy} rx={rx} ry={ry} fill="white" />
          <ellipse cx={cx} cy={cy} rx={irx} ry={iry} fill="black" />
        </mask>

        {/* ── Donut mask (slightly larger for rim effects) ── */}
        <mask id={`donut-wide-${uid}`}>
          <ellipse cx={cx} cy={cy} rx={rx + 12} ry={ry + 8} fill="white" />
          <ellipse cx={cx} cy={cy} rx={irx - 8} ry={iry - 6} fill="black" />
        </mask>

        {/* ── Inner center — dark recess (void of table center) ── */}
        <radialGradient id={`center-void-${uid}`} cx="50%" cy="45%" r="55%">
          <stop offset="0%"   stopColor="#1C1810" stopOpacity="0.3" />
          <stop offset="60%"  stopColor="#080706" stopOpacity="0.7" />
          <stop offset="100%" stopColor="#040302" stopOpacity="0.95" />
        </radialGradient>
      </defs>

      {/* ═══ Cast shadow of table onto marble floor ═══ */}
      <ellipse
        cx={cx + 10} cy={cy + 28}
        rx={rx * 0.96} ry={ry * 0.96}
        fill="rgba(0,0,0,0.42)"
        style={{ filter: 'blur(28px)' }}
      />
      <ellipse
        cx={cx + 5} cy={cy + 12}
        rx={rx} ry={ry}
        fill="rgba(0,0,0,0.22)"
        style={{ filter: 'blur(10px)' }}
      />

      {/* ═══ Table surface — polished obsidian ═══ */}
      <ellipse
        cx={cx} cy={cy} rx={rx} ry={ry}
        fill={`url(#obsidian-${uid})`}
        mask={`url(#donut-${uid})`}
      />

      {/* ═══ Skylight reflection on surface (physical light) ═══ */}
      <ellipse
        cx={cx} cy={cy} rx={rx} ry={ry}
        fill={`url(#skylight-refl-${uid})`}
        mask={`url(#donut-${uid})`}
        style={{ mixBlendMode: 'screen' as React.CSSProperties['mixBlendMode'] }}
      />

      {/* ═══ Table outer rim bevel — warm stone-light catch ═══ */}
      <ellipse
        cx={cx} cy={cy} rx={rx + 4} ry={ry + 3}
        fill="none"
        stroke="rgba(200,175,120,0.22)"
        strokeWidth="10"
        style={{ filter: 'blur(3px)' }}
      />

      {/* ═══ Table inner rim shadow — adds thickness / depth ═══ */}
      <ellipse
        cx={cx} cy={cy} rx={irx - 2} ry={iry - 2}
        fill="none"
        stroke="rgba(0,0,0,0.6)"
        strokeWidth="12"
        style={{ filter: 'blur(6px)' }}
      />

      {/* ═══ Center void — deep dark recess ═══ */}
      <ellipse
        cx={cx} cy={cy} rx={irx} ry={iry}
        fill={`url(#center-void-${uid})`}
      />
      {/* Marble floor showing through center void */}
      <ellipse
        cx={cx} cy={cy} rx={irx - 6} ry={iry - 4}
        fill="rgba(195,185,165,0.04)"
      />

      {/* ═══ Dept Arcs = PHYSICALLY CARVED BRASS CHANNELS ═══ */}
      {/* These are recessed channels in the table surface,       */}
      {/* not floating digital rings.                             */}
      {deptArcs.map(arc => {
        const x1 = cx + brassR * Math.cos(arc.startAngle);
        const y1 = cy + brassRY * Math.sin(arc.startAngle);
        const x2 = cx + brassR * Math.cos(arc.endAngle);
        const y2 = cy + brassRY * Math.sin(arc.endAngle);
        const large = (arc.endAngle - arc.startAngle) > Math.PI ? 1 : 0;
        const pathD = `M ${x1} ${y1} A ${brassR} ${brassRY} 0 ${large} 1 ${x2} ${y2}`;

        return (
          <g key={arc.dept}>
            {/* Channel shadow — the recession carved into obsidian */}
            <path d={pathD} fill="none"
              stroke="rgba(0,0,0,0.65)" strokeWidth="7" strokeLinecap="round"
              style={{ filter: 'blur(2px)' }} />
            {/* Channel body — dark carved groove */}
            <path d={pathD} fill="none"
              stroke="#0A0806" strokeWidth="5" strokeLinecap="round" />
            {/* Brass lining — brushed metal in the groove */}
            <path d={pathD} fill="none"
              stroke={arc.color} strokeWidth="2.8" strokeLinecap="round"
              opacity="0.85"
              style={{ filter: `drop-shadow(0 1px 4px ${arc.color}88)` }} />
            {/* Ambient table reflection of the glowing ring */}
            <path d={pathD} fill="none"
              stroke={arc.color} strokeWidth="12" strokeLinecap="round"
              opacity="0.12"
              style={{ filter: `blur(8px)` }} />
            {/* Specular highlight — skylight catching brass surface */}
            <path d={pathD} fill="none"
              stroke="rgba(255,248,220,0.25)" strokeWidth="0.8" strokeLinecap="round"
              strokeDasharray="3 8" />
          </g>
        );
      })}
    </svg>
  );
}

/* ══════════════════════════════════════════════════════════════════
   DeptLegend — Spatially anchored frosted glass panel (right)
   Typography appears etched into glass.
   ══════════════════════════════════════════════════════════════════ */
function DeptLegend() {
  const entries: { dept: Department; color: string; label: string }[] = [
    { dept: 'agentic_factory', color: '#D92525', label: 'Agentic Factory' },
    { dept: 'lead_gen', color: '#4A9EFF', label: 'Lead Gen' },
    { dept: 'research_intelligence', color: '#9B59B6', label: 'Research & Intel' },
    { dept: 'client_delivery', color: '#00C896', label: 'Client Delivery' },
    { dept: 'recursive_self_improvement', color: '#B8860B', label: 'Self-Improvement' },
  ];

  return (
    <div style={{
      position: 'absolute', top: 24, right: 24, zIndex: 35,
      padding: '16px 20px',
      display: 'flex', flexDirection: 'column', gap: 10,
      // Spatial frosted glass — heavily blurs stone/columns behind
      background: 'rgba(14, 11, 6, 0.45)',
      backdropFilter: 'blur(40px) saturate(1.4) brightness(1.1)',
      WebkitBackdropFilter: 'blur(40px) saturate(1.4) brightness(1.1)',
      // Thin specular edge — glass catching skylight
      border: '1px solid rgba(220,200,155,0.15)',
      borderTop: '1px solid rgba(255,248,220,0.28)',
      borderLeft: '1px solid rgba(255,248,220,0.18)',
      borderRadius: 10,
      boxShadow: '0 16px 48px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,250,230,0.08)',
    }}>
      <div style={{
        fontSize: 8, fontWeight: 600, letterSpacing: '0.18em',
        color: 'rgba(220,200,155,0.7)', textTransform: 'uppercase',
        marginBottom: 4,
        borderBottom: '0.5px solid rgba(220,200,155,0.15)',
        paddingBottom: 6,
        fontFamily: 'var(--font-sans)',
        // Text "etched" into glass
        textShadow: '0 1px 2px rgba(255,255,255,0.08)',
      }}>
        Departments
      </div>
      {entries.map(e => (
        <div key={e.dept} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* Physical brass-look channel swatch */}
          <div style={{
            width: 18, height: 3, borderRadius: 1,
            background: `linear-gradient(to bottom, rgba(255,248,220,0.3) 0%, ${e.color} 30%, ${e.color}99 70%, rgba(0,0,0,0.2) 100%)`,
            boxShadow: `0 1px 4px ${e.color}66`,
          }} />
          <span style={{
            fontSize: 10, fontWeight: 500, letterSpacing: '0.06em',
            color: 'rgba(235,225,205,0.85)', textTransform: 'uppercase',
            fontFamily: 'var(--font-sans)',
            textShadow: '0 1px 3px rgba(0,0,0,0.7)',
          }}>
            {e.label}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════
   ToolsLegend — Spatial frosted glass panel (left)
   ══════════════════════════════════════════════════════════════════ */
function ToolsLegend() {
  const tools = [
    { id: 'anthropic',  label: 'Anthropic (Claude)',       icon: 'M12 4c-4.4 0-8 3.6-8 8s3.6 8 8 8 8-3.6 8-8-3.6-8-8-8zM12 16c-2.2 0-4-1.8-4-4s1.8-4 4-4 4 1.8 4 4-1.8 4-4 4z' },
    { id: 'openai',     label: 'OpenAI',                   icon: 'M12.5 12.1a2.1 2.1 0 001.3-.9 2.2 2.2 0 00.3-1.6 2.3 2.3 0 00-1-1.3l-2.4-1.4V4.3a2.3 2.3 0 00-1.1-2 2.2 2.2 0 00-2.2 0l-2.5 1.5a2.2 2.2 0 00-1.1 2v2.8L1.4 10A2.3 2.3 0 00.3 12a2.2 2.2 0 001 1.7l2.5 1.4v2.8a2.3 2.3 0 001.1 2 2.2 2.2 0 002.2 0l2.5-1.5a2.2 2.2 0 001.1-2v-2.8l2.4-1.4c.5-.3.9-.8 1-1.3s.1-1.1-.2-1.6zm-5-8.8a1.2 1.2 0 01.6-1.1 1.3 1.3 0 011.2 0l2.5 1.5a1.3 1.3 0 01.6 1.1V6.2L9.9 7.6a1.3 1.3 0 01-1.2 0L6.2 6.2zm-2.4 4l2.5-1.5a1.3 1.3 0 011.2 0l2.5 1.5v2.8L7.5 14.5a1.3 1.3 0 01-1.2 0L3.8 13zm0 5.6V10.1l2.5 1.5v2.8zm2.4 4a1.2 1.2 0 01-.6-1.1V17.8l2.5-1.4a1.3 1.3 0 011.2 0l2.5 1.4v2.8zm7.4-4.8a1.3 1.3 0 01-.6 1.1l-2.5 1.5V17.8l-2.5-1.5zm2.5-5.6a1.2 1.2 0 01.6 1.1c0 .4-.1.8-.3 1.1l-2.5 1.5V13zm-5-3.2a1.3 1.3 0 01.6-1.1l2.5-1.5V7.6l-2.5 1.5z' },
    { id: 'supabase',   label: 'Supabase',                 icon: 'M12 4L4 12l8 8 8-8-8-8zm0 18l-8-8 8 8 8-8-8 8z' },
    { id: 'discord',    label: 'Discord Bot',              icon: 'M19 4H5a2 2 0 00-2 2v14l4-4h12a2 2 0 002-2V6a2 2 0 00-2-2zm-9 9H8v-2h2v2zm4 0h-2v-2h2v2z' },
    { id: 'oauth',      label: 'Google OAuth',             icon: 'M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z' },
    { id: 'pinecone',   label: 'Pinecone',                 icon: 'M12 2L4 9l8 7 8-7-8-7zm0 10.5L6.5 9 12 4.16 17.5 9 12 12.5zM12 18.5l-5-4.4v2.9l5 4.5 5-4.5v-2.9l-5 4.4z' },
    { id: 'hunter',     label: 'Hunter API',               icon: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm0-13c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zm0 8c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3z' },
    { id: 'serp',       label: 'SerpAPI',                  icon: 'M15.5 14h-.79l-.28-.27C15.41 12.59 16 11.11 16 9.5 16 5.91 13.09 3 9.5 3S3 5.91 3 9.5 5.91 16 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z' },
    { id: 'firecrawl',  label: 'Firecrawl',                icon: 'M12.01 21.49C16.25 18.25 19 14.5 19 11c0-4.5-3.5-8-8-8S3 6.5 3 11c0 3.5 2.75 7.25 6.99 10.49.59.45 1.43.45 2.02 0zM12 5c3.31 0 6 2.69 6 6 0 2.59-2.12 5.67-6 8.58-3.88-2.91-6-5.99-6-8.58 0-3.31 2.69-6 6-6z' },
    { id: 'github',     label: 'GitHub',                   icon: 'M12 2C6.477 2 2 6.477 2 12c0 4.42 2.865 8.166 6.839 9.489.5.092.682-.217.682-.482 0-.237-.008-.866-.013-1.7-2.782.603-3.369-1.34-3.369-1.34-.454-1.156-1.11-1.464-1.11-1.464-.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.831.092-.646.35-1.086.636-1.336-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.294 2.747-1.025 2.747-1.025.546 1.377.203 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.842-2.339 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.336-.012 2.415-.012 2.743 0 .267.18.577.688.479C19.138 20.164 22 16.418 22 12c0-5.523-4.477-10-10-10z' },
    { id: 'langfuse',   label: 'Langfuse',                 icon: 'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zM9 17H7v-7h2v7zm4 0h-2V7h2v10zm4 0h-2v-4h2v4z' },
  ];

  return (
    <div style={{
      position: 'absolute', top: 12, left: 12, zIndex: 35,
      padding: '10px 14px',
      display: 'flex', flexDirection: 'column', gap: 5,
      minWidth: 152,
      background: 'rgba(14, 11, 6, 0.45)',
      backdropFilter: 'blur(40px) saturate(1.4) brightness(1.1)',
      WebkitBackdropFilter: 'blur(40px) saturate(1.4) brightness(1.1)',
      border: '1px solid rgba(220,200,155,0.15)',
      borderTop: '1px solid rgba(255,248,220,0.28)',
      borderLeft: '1px solid rgba(255,248,220,0.18)',
      borderRadius: 10,
      boxShadow: '0 16px 48px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,250,230,0.08)',
    }}>
      <div style={{
        fontSize: 8, fontWeight: 600, letterSpacing: '0.18em',
        color: 'rgba(220,200,155,0.7)', textTransform: 'uppercase',
        marginBottom: 3,
        borderBottom: '0.5px solid rgba(220,200,155,0.15)',
        paddingBottom: 5,
        fontFamily: 'var(--font-sans)',
        textShadow: '0 1px 2px rgba(255,255,255,0.08)',
      }}>
        Agent Tools
      </div>
      {tools.map(t => (
        <div key={t.id} style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <div style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: 'radial-gradient(circle at 30% 30%, #FFEBA0 0%, #D4AF37 40%, #8A640F 90%, #523906 100%)',
            boxShadow: '0 0 5px rgba(212,175,55,0.8), inset 0 1px 0 rgba(255,255,255,0.2)',
            border: '0.5px solid rgba(220,200,155,0.4)',
            flexShrink: 0,
          }} />
          <span style={{
            fontSize: 8.5, fontWeight: 500, letterSpacing: '0.04em',
            color: 'rgba(230,215,185,0.75)', textTransform: 'uppercase',
            fontFamily: 'var(--font-sans)',
            textShadow: '0 1px 3px rgba(0,0,0,0.8)',
          }}>
            {t.label}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════
   MAIN EXPORT
   ══════════════════════════════════════════════════════════════════ */
export function RoundTable({
  agents, handoffs, metrics, activity, onSelectAgent, selectedAgentId
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 1200, h: 800 });
  const [hoveredAgent, setHoveredAgent] = useState<string | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect;
      setSize({ w: width, h: height });
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const tableAgents = useMemo(() => {
    const result: AgentDef[] = [];
    UNIFIED_TABLE_DEPTS.forEach(d => result.push(...AGENT_DEFS.filter(a => a.department === d)));
    return result;
  }, []);

  const rx = Math.min(size.w * 0.28, 350);
  const ry = Math.min(size.h * 0.24, 210);
  const innerScale = 0.55;

  const tcx = size.w * 0.5;
  const tcy = size.h * 0.54;

  const tSeats = useMemo(() =>
    tableAgents.map((_, i) => getSeatPosition(i, tableAgents.length, tcx, tcy, rx, ry, 1)),
    [tableAgents.length, tcx, tcy, rx, ry]
  );

  const seatMap = useMemo(() => {
    const map: Record<string, SeatPosition> = {};
    tableAgents.forEach((a, i) => { map[a.id] = tSeats[i]; });
    return map;
  }, [tableAgents, tSeats]);

  return (
    <div
      ref={containerRef}
      id="round-table-view"
      style={{
        width: '100%', height: '100%',
        position: 'relative', overflow: 'hidden',
        background: '#0A0805',  // Obsidian fallback while image loads
        isolation: 'isolate',   // Forces a local stacking context for children
        zIndex: 1,              // Browser-fallback stacking context
      }}
    >
      {/* ══ LAYER 0: Photorealistic Chamber + Light SVG Overlays ══ */}
      <GrandHallCanvas
        width={size.w}
        height={size.h}
        cx={tcx}
        cy={tcy}
      />

      {/* ══ LAYER 1: Physical Table Surface ══ */}
      <PhysicalOnyxTable
        cx={tcx} cy={tcy} rx={rx} ry={ry}
        innerScale={innerScale}
        agents={tableAgents} depts={UNIFIED_TABLE_DEPTS} seats={tSeats}
      />

      {/* ══ LAYER 2: UI Legends (spatial frosted glass) ══ */}
      <ToolsLegend />
      <DeptLegend />

      {/* ══ LAYER 2.5: Active count — minimal spatial text ══ */}
      <div style={{
        position: 'absolute', top: 20, left: '50%', transform: 'translateX(-50%)',
        textAlign: 'center', zIndex: 55,
        padding: '8px 24px',
        background: 'rgba(14, 11, 6, 0.45)',
        backdropFilter: 'blur(40px) saturate(1.4)',
        WebkitBackdropFilter: 'blur(40px) saturate(1.4)',
        border: '1px solid rgba(220,200,155,0.15)',
        borderTop: '1px solid rgba(255,248,220,0.28)',
        borderRadius: 8,
        boxShadow: '0 12px 40px rgba(0,0,0,0.5)',
      }}>
        <div style={{
          fontSize: 8, fontWeight: 600, letterSpacing: '0.22em',
          color: 'rgba(210,185,130,0.65)', textTransform: 'uppercase',
          marginBottom: 2, fontFamily: 'var(--font-sans)',
          textShadow: '0 1px 3px rgba(0,0,0,0.8)',
        }}>
          {metrics.currentClient}
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, justifyContent: 'center' }}>
          <span style={{
            fontSize: 28, fontWeight: 700, lineHeight: 1,
            color: '#E8D5A0',
            fontFamily: 'var(--font-sans)',
            textShadow: '0 2px 12px rgba(220,195,130,0.5)',
          }}>
            {metrics.activeNow}
          </span>
          <span style={{
            fontSize: 9, fontWeight: 500,
            color: 'rgba(210,185,130,0.55)',
            textTransform: 'uppercase', letterSpacing: '0.1em',
            fontFamily: 'var(--font-sans)',
          }}>
            Active
          </span>
        </div>
      </div>

      {/* ══ LAYER 3: Agent drop shadows + table reflections (SVG) ══ */}
      <svg style={{
        position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
        pointerEvents: 'none', zIndex: 18, overflow: 'visible',
      }}>
        {tableAgents.map(def => {
          const seat = seatMap[def.id];
          if (!seat) return null;
          const agentState = agents[def.id];
          const isActive = agentState?.status === 'active' || agentState?.status === 'handoff';
          const deptColor = getDeptColor(def.department);
          return (
            <g key={`shadow-${def.id}`}>
              {/* Agent body reflection on polished table */}
              <ellipse cx={seat.x} cy={seat.y + 40} rx={16} ry={8} fill="rgba(255,255,255,0.06)" style={{ filter: 'blur(6px)', mixBlendMode: 'screen' }} />
              <AgentTableShadow x={seat.x} y={seat.y} isActive={isActive} />
              <ActiveTableReflection x={seat.x} y={seat.y} color={deptColor} isActive={isActive} />
            </g>
          );
        })}
      </svg>

      {/* ══ LAYER 4: Brass Channel Handoff Streams ══ */}
      <svg style={{
        position: 'absolute', top: 0, left: 0, width: '100%', height: '100%',
        pointerEvents: 'none', zIndex: 20, overflow: 'visible',
      }}>
        {handoffs.map((link, i) => {
          const from = seatMap[link.fromAgentId];
          const to = seatMap[link.toAgentId];
          if (!from || !to) return null;
          const fromState = agents[link.fromAgentId];
          const isActive = fromState?.status === 'handoff' && fromState?.downstreamAgent === link.toAgentId;
          return (
            <BrassChannelHandoff
              key={`${link.fromAgentId}-${link.toAgentId}-${i}`}
              from={from} to={to}
              color={link.departmentColor}
              isActive={isActive}
            />
          );
        })}
      </svg>

      {/* ══ LAYER 5: Agent Cards — 100% locked ══ */}
      {tableAgents.map(def => {
        const seat = seatMap[def.id];
        if (!seat) return null;
        const agentState = agents[def.id];
        if (!agentState) return null;

        const deptColor = getDeptColor(def.department);
        const isSelected = selectedAgentId === def.id;
        const isHovered = hoveredAgent === def.id;

        return (
          <div
            key={def.id}
            onMouseEnter={() => setHoveredAgent(def.id)}
            onMouseLeave={() => setHoveredAgent(null)}
            onClick={() => onSelectAgent(isSelected ? null : def.id)}
            style={{
              position: 'absolute',
              left: seat.x, top: seat.y,
              transform: 'translate(-50%, -50%)',
              cursor: 'pointer',
              zIndex: isHovered || isSelected ? 1000 : Math.round(seat.y),
            }}
          >
            {/* Spatial glass tooltip */}
            {(isHovered || isSelected) && (
              <div className="fade-in" style={{
                position: 'absolute', bottom: '100%', left: '50%',
                transform: 'translateX(-50%)', marginBottom: 12,
                padding: '12px 16px', width: 240, pointerEvents: 'none',
                background: 'rgba(12, 9, 4, 0.72)',
                backdropFilter: 'blur(40px) saturate(1.4)',
                WebkitBackdropFilter: 'blur(40px) saturate(1.4)',
                border: '1px solid rgba(220,200,155,0.18)',
                borderTop: '1px solid rgba(255,248,220,0.32)',
                borderRadius: 8,
                boxShadow: '0 16px 48px rgba(0,0,0,0.65)',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{
                    fontSize: 12, fontWeight: 600,
                    color: 'rgba(245,235,210,0.95)',
                    fontFamily: 'var(--font-sans)',
                    letterSpacing: '0.04em',
                    textShadow: '0 1px 4px rgba(0,0,0,0.8)',
                  }}>
                    {def.name}
                  </span>
                  <span style={{
                    fontSize: 11, color: deptColor, fontWeight: 700,
                    fontFamily: 'var(--font-mono)',
                    textShadow: `0 0 8px ${deptColor}66`,
                  }}>
                    {(agentState.confidenceScore * 100).toFixed(0)}%
                  </span>
                </div>
                {agentState.currentTask && (
                  <div style={{
                    fontSize: 11, color: 'rgba(220,205,175,0.65)',
                    lineHeight: 1.5, fontFamily: 'var(--font-sans)',
                  }}>
                    {agentState.currentTask}
                  </div>
                )}
              </div>
            )}

            <AgentCard
              id={def.id}
              name={def.name}
              departmentLabel={DEPARTMENTS[def.department]?.shortLabel}
              color={deptColor}
              status={agentState.status}
              confidence={agentState.confidenceScore}
              angle={seat.angle}
              planned={def.planned === true}
            />
          </div>
        );
      })}
    </div>
  );
}
