/* ══════════════════════════════════════════════════════════════════
   AgentCard — Seated Agent Figure with Floating Role Icon
   Premium "Secret Service Boardroom" aesthetic
   ══════════════════════════════════════════════════════════════════ */

import { useMemo } from 'react';

interface AgentCardProps {
  id: string;
  name: string;
  departmentLabel: string;
  color: string;
  status: 'active' | 'idle' | 'waiting' | 'error' | 'handoff' | 'hitl_pending';
  confidence: number;
  angle?: number;
  planned?: boolean;
}

const ROLE_ICONS: Record<string, string> = {
  // ── AGENTIC FACTORY ──
  'build-orchestrator': 'M24 4c-11 0-20 9-20 20s9 20 20 20 20-9 20-20S35 4 24 4zm0 6a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm0 22a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm-12-8a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm24 0a3 3 0 1 1 0 6 3 3 0 0 1 0-6z', // Central control network
  'builder-agent':      'M18 14l-10 10 10 10M30 14l10 10-10 10M27 8l-6 32', // Code tags </> (Coder)
  'validator-agent':    'M24 4L8 10v12c0 9 7 17 16 20 9-3 16-11 16-20V10L24 4zm-2 26l-6-6 2.8-2.8 3.2 3.2 8.2-8.2 2.8 2.8-11 11z', // validator-shield
  'deployer-agent':     'M24 4s-9 7-9 17c0 4 2 8 5 11v8l4-4 4 4v-8c3-3 5-7 5-11 0-10-9-17-9-17zm-3 18a3 3 0 1 1 6 0 3 3 0 0 1-6 0z', // rocket liftoff
  'aria':               'M24 14a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM14 28a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM34 28a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM24 36a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM10 18a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM38 18a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM24 20v16M16 29l7-8M32 29l-7-8M12 21l10-3M36 21l-10-3', // Central Orchestrator

  // ── LEAD GEN ──
  'hq-lead-scraping':   'M18 18c0-3.3 2.7-6 6-6s6 2.7 6 6-2.7 6-6 6-6-2.7-6-6zm12 12H12v-2c0-4 4-6 12-6s12 2 12 6v2zm-2-12a10 10 0 1 1-2-5.7l6.7-6.7 2.8 2.8-6.7 6.7c.6.9 1 2 1 3.2z', // Magnifying glass over profile
  'lead-scraper':       'M12 6h16l10 10v26H12V6zm20 12h-8v-8M22 22l-6 10h6v6l6-10h-6v-6z', // Spark database
  'icp-scoring':        'M24 4a20 20 0 1 0 20 20A20 20 0 0 0 24 4zm0 34a14 14 0 1 1 14-14 14 14 0 0 1-14 14zm0-24a10 10 0 1 0 10 10A10 10 0 0 0 24 10zm-3 9l3 3 6-6 1.5 1.5L24 23.5l-4.5-4.5z', // Scorer target
  'linkedin-outreach':  'M4 20l38-16-16 38-6-16L4 20zM22 22l14-6-8 8-6-2z', // Outreach paper airplane
  'crm-nurture':        'M4 12h40v24H4V12zm20 14L8 16v20h32V16l-16 10zm0-3l16-11H8l16 11z', // Envelope sequence
  'biz-dev-outreach':   'M8 14h32v24H8V14zm16 6c-3.3 0-6 2.7-6 6v12h12V26c0-3.3-2.7-6-6-6zm-12 0h8v4h-8v-4zm24 0h8v4h-8v-4z', // Executive briefcase

  // ── RESEARCH & INTELLIGENCE ──
  'market-mapper':      'M10 10l8 4v24l-8-4V10zM18 14l12-4v24l-12 4V14zM30 10l8 4v24l-8-4V10zM22 16v4M22 24v4', // mapper
  'market-watcher':     'M24 8C12 8 4 16 4 24s8 16 16 16 20-8 20-16S36 8 24 8zm0 24a8 8 0 1 1 8-8 8 8 0 0 1-8 8zm0-12a4 4 0 1 0 4 4 4 4 0 0 0-4-4z', // Sentinel eye
  'oss-scout':          'M18 10h12v4H18v-4zM6 16h36v22H6V16zm6 6h4v4h-4v-4zm8 0h16v2H20v-2zm0 6h12v2H20v-2z', // OSS vault
  'strategic-arch':     'M16 8h4v4h4v-4h4v4h4v-4h4v6h-2l-2 6h-12l-2-6h-2V8zM18 20h12v4h-12zM16 24h16l2 14h-20l2-14z', // strategist
  'competitive-intel':  'M10 14a6 6 0 1 0 12 0 6 6 0 0 0-12 0zm16 0a6 6 0 1 0 12 0 6 6 0 0 0-12 0zm-8 4v16h4V18h-4zm-8 16h24v4H10v-4z', // Binoculars

  // ── CLIENT DELIVERY ──
  'offer-matching':     'M12 6h24v28H12V6zm4 6h16v2H16v-2zm0 6h16v2H16v-2zm0 6h10v2H16v-2zm8 10a4 4 0 1 1-8 0 4 4 0 0 1 8 0z', // Certified contract
  'meeting-intel':      'M8 6h24l8 8v28H8V6zm6 8h12v2H14v-2zm0 8h20v2H14v-2zm0 8h20v2H14v-2z', // Quill paper
  'outcome-attribution':'M6 40h36v2H6v-2zm6-4h6v-8h-6v8zm10-10h6v18h-6v-18zm10-8h6v26h-6V18zm10-8h6v34h-6V10z', // attribution chart
  'client-onboarding':  'M8 4h20l12 10v30H8V4zm6 10h8v2h-8v-2zm0 8h16v2H14v-2zm0 8h16v2H14v-2z', // onboarding door
  'client-success':     'M12 6h24v6H12V6zm2 10h20v8c0 6-5 11-10 11S14 32 14 24v-8zm10 22h-4v4h8v-4h-4z', // success trophy

  // ── RECURSIVE SELF-IMPROVEMENT ──
  'eval-telemetry':     'M24 4a20 20 0 1 0 20 20A20 20 0 0 0 24 4zm0 6a14 14 0 1 1-14 14 14 14 0 0 1 14-14zm0 4v10l6 6', // radar target
  'healer-agent':       'M24 4C13 4 4 13 4 24s9 20 20 20 20-9 20-20S35 4 24 4zm8 22h-6v6h-4v-6h-6v-4h6v-6h4v6h6v4z', // healer-cross
  'trainer-agent':      'M24 6L4 16l20 10 20-10L24 6zm-16 16v8c0 4 7 7 16 7s16-3 16-7v-8l-16 8-16-8z', // trainer-hat
  'auditor-agent':      'M24 4a10 10 0 0 0-10 10v6h20v-6a10 10 0 0 0-10-10zm-6 16v-6a6 6 0 0 1 12 0v6H18zm20 4H10a4 4 0 0 0-4 4v12a4 4 0 0 0 4 4h28a4 4 0 0 0 4-4V28a4 4 0 0 0-4-4zm-12 14h-4v-6h4v6z', // auditor-padlock
};

function getIconPath(agentId: string): string {
  return ROLE_ICONS[agentId] || ROLE_ICONS['aria'];
}

export function AgentCard({ id, name, departmentLabel, color, status, confidence, angle = 0, planned = false }: AgentCardProps) {
  const isHitl = status === 'hitl_pending' && !planned;
  const isError = status === 'error' && !planned;
  const isActive = (status === 'active' || status === 'handoff') && !planned;

  const iconPath = useMemo(() => getIconPath(id), [id]);

  let norm = angle % (2 * Math.PI);
  if (norm < 0) norm += 2 * Math.PI;

  let imgSrc = '/agent-front.png';
  let flip = false;

  // Specific manual overrides requested by the user:
  // Proposer (offer-matching) and Onboard should flip to face inward (like right-side agents).
  // Scribe (meeting-intel), Nurture, Biz Dev, and Mapper should face inward/upward (back view).
  if (id === 'validator-agent' || id === 'trainer-agent') {
    imgSrc = '/agent-front.png';
    flip = false;
  } else if (
    id === 'offer-matching' ||
    id === 'client-onboarding' ||
    id === 'eval-telemetry' ||
    id === 'healer-agent'
  ) {
    imgSrc = '/agent-tilt.png'; 
    flip = true;
  } else if (id === 'crm-nurture') {
    imgSrc = '/agent-tilt.png';
    flip = false;
  } else if (id === 'market-mapper' || id === 'biz-dev-outreach' || id === 'meeting-intel') {
    imgSrc = '/agent-back.png';
    flip = false;
  } else if (norm <= 0.35 * Math.PI || norm > 1.65 * Math.PI) {
    imgSrc = '/agent-tilt.png'; flip = false;   // Right → face left (inward)
  } else if (norm > 0.35 * Math.PI && norm <= 0.85 * Math.PI) {
    imgSrc = '/agent-back.png';                  // Bottom → back to camera
  } else if (norm > 0.85 * Math.PI && norm <= 1.15 * Math.PI) {
    imgSrc = '/agent-tilt.png'; flip = true;    // Left → face right (inward)
  } else {
    imgSrc = '/agent-front.png';                 // Top → facing camera
  }

  return (
    <div
      className={`agent-figure-container ${status}${planned ? ' planned' : ''}`}
      style={{
        position: 'relative',
        width: 120,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        opacity: planned ? 0.38 : 1,
        filter: planned ? 'grayscale(0.7)' : undefined,
        '--glow-color': color,
      } as React.CSSProperties}
    >
      {planned && (
        <div
          className="sans"
          style={{
            position: 'absolute',
            top: -10,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 14,
            padding: '2px 8px',
            borderRadius: 4,
            background: 'rgba(0,0,0,0.7)',
            color: '#FFD27A',
            fontSize: 8,
            fontWeight: 900,
            letterSpacing: '0.12em',
            textTransform: 'uppercase' as const,
            border: '1px solid rgba(255, 210, 122, 0.5)',
            whiteSpace: 'nowrap',
          }}
        >
          PLANNED
        </div>
      )}
      {/* ── Floating Role Icon Medallion (above head) ── */}
      <div style={{
        position: 'relative',
        zIndex: 12,
        marginBottom: 12,
      }}>
        <svg
          width="34" height="34"
          viewBox="0 0 48 48"
          style={{
            filter: 'drop-shadow(0 2px 6px rgba(0,0,0,0.25))',
            transition: 'all 0.3s ease',
          }}
        >
          <defs>
            <linearGradient id={`icon-bg-${id}`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#FFFFFF" />
              <stop offset="100%" stopColor="#E8ECEF" />
            </linearGradient>
          </defs>
          {/* Silver medallion disc */}
          <circle cx="24" cy="24" r="22" fill={`url(#icon-bg-${id})`} />
          {/* Dept color ring */}
          <circle cx="24" cy="24" r="22" fill="none" stroke={color} strokeWidth="2.5" opacity="0.8" />
          {/* Inner silver ring */}
          <circle cx="24" cy="24" r="19" fill="none" stroke="rgba(255,255,255,0.9)" strokeWidth="1" />
          {/* Role icon */}
          <path d={iconPath} fill="#2C3A47" fillRule="evenodd" stroke="#2C3A47" strokeWidth="0.3" />
        </svg>

        {/* Status orb on the icon */}
        <div style={{
          position: 'absolute', top: -2, right: -2, zIndex: 13,
        }}>
          <span className={`status-orb ${status}`} style={{
            border: '2px solid #FFF',
            width: 10, height: 10,
          }} />
        </div>
      </div>

      {/* ── HITL Pulsing Ring (around icon) ── */}
      {isHitl && (
        <div style={{
          position: 'absolute', top: -4, left: '50%', transform: 'translateX(-50%)',
          width: 42, height: 42, borderRadius: '50%',
          border: '2px solid #FF9500',
          animation: 'hitl-ring-pulse 1.5s infinite',
          zIndex: 11,
        }} />
      )}

      {/* ── Active Processing Ring (around icon) ── */}
      {isActive && (
        <div style={{
          position: 'absolute', top: -6, left: '50%', transform: 'translateX(-50%)',
          width: 46, height: 46, borderRadius: '50%',
          border: `1.5px dashed ${color}`,
          borderLeftColor: 'transparent',
          animation: 'spin 4s linear infinite',
          zIndex: 10,
          opacity: 0.8,
        }} />
      )}

      {/* ── Name label (floating, placed above head for perfect visibility) ── */}
      <div style={{
        zIndex: 15,
        textAlign: 'center',
        marginTop: -6,
        marginBottom: 8,
      }}>
        <span className="sans" style={{
          fontSize: 9,
          fontWeight: 900,
          color: '#FFFFFF',
          textAlign: 'center',
          lineHeight: 1.1,
          letterSpacing: '0.06em',
          textTransform: 'uppercase' as const,
          textShadow: '0 1px 4px rgba(0,0,0,0.9), 0 0 8px rgba(0,0,0,0.9)',
        }}>
          {name}
        </span>
      </div>

      {/* ── Seated Agent Figure (no box, transparent) ── */}
      <div style={{
        width: 90,
        height: 90,
        position: 'relative',
        marginBottom: -10,
        zIndex: 5,
        overflow: 'visible',
      }}>
        <img
          src={`${imgSrc}?v=3`}
          alt={name}
          style={{
            position: 'absolute',
            bottom: 0,
            left: '50%',
            transform: `translateX(-50%)${flip ? ' scaleX(-1)' : ''}`,
            width: 100,
            height: 'auto',
            objectFit: 'contain',
            opacity: 1.0,
            transition: 'all 0.3s ease',
            filter: status === 'active'
              ? `drop-shadow(0 0 12px ${color}) drop-shadow(0 0 6px ${color})`
              : `drop-shadow(0 3px 6px rgba(0,0,0,0.6))`,
          }}
        />
      </div>
    </div>
  );
}
