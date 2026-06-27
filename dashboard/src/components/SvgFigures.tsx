import React from 'react';

export type FigureArchetype = 'factory' | 'leadgen' | 'research' | 'delivery' | 'rsi';

interface AgentFigureProps {
  archetype: FigureArchetype;
  color: string;
  status: 'active' | 'idle' | 'waiting' | 'error' | 'handoff' | 'hitl_pending';
  confidence: number;
}

export function AgentFigure({ archetype, color, status, confidence }: AgentFigureProps) {
  const isHitl = status === 'hitl_pending';
  const isError = status === 'error';
  const isActive = status === 'active' || status === 'handoff';

  // Base chair
  const chair = (
    <path
      d="M30 90 L30 30 C30 20, 70 20, 70 30 L70 90 Z"
      fill="#111"
      stroke="#222"
      strokeWidth={2}
    />
  );

  // Silhouettes based on archetype
  const renderSilhouette = () => {
    switch (archetype) {
      case 'factory':
        // Upright, formal
        return (
          <g>
            <circle cx="50" cy="35" r="10" fill="#2a2a2a" />
            <path d="M40 50 C40 45, 60 45, 60 50 L65 90 L35 90 Z" fill="#222" />
            <path d="M48 50 L50 65 L52 50 Z" fill="#111" opacity={0.5} /> {/* Tie suggestion */}
          </g>
        );
      case 'leadgen':
        // Leaning forward
        return (
          <g>
            <circle cx="53" cy="38" r="10" fill="#2a2a2a" />
            <path d="M42 53 C42 48, 62 48, 64 53 L70 90 L38 90 Z" fill="#222" />
            <path d="M42 53 Q 50 60 64 53" stroke="#1a1a1a" strokeWidth="2" fill="none" />
          </g>
        );
      case 'research':
        // Contemplative (hand near chin)
        return (
          <g>
            <circle cx="50" cy="36" r="10" fill="#2a2a2a" />
            <path d="M40 52 C40 47, 60 47, 60 52 L65 90 L35 90 Z" fill="#222" />
            <path d="M35 65 Q 40 50 48 42" stroke="#222" strokeWidth="4" strokeLinecap="round" fill="none" />
          </g>
        );
      case 'delivery':
        // Open posture
        return (
          <g>
            <circle cx="50" cy="34" r="10" fill="#2a2a2a" />
            <path d="M38 52 C38 46, 62 46, 62 52 L68 90 L32 90 Z" fill="#222" />
            <path d="M38 52 Q 30 65 35 75" stroke="#222" strokeWidth="4" strokeLinecap="round" fill="none" />
            <path d="M62 52 Q 70 65 65 75" stroke="#222" strokeWidth="4" strokeLinecap="round" fill="none" />
          </g>
        );
      case 'rsi':
        // Observing, head slightly tilted
        return (
          <g transform="rotate(3 50 35)">
            <circle cx="50" cy="35" r="10" fill="#2a2a2a" />
            <path d="M41 50 C41 45, 59 45, 59 50 L63 90 L37 90 Z" fill="#222" />
          </g>
        );
      default:
        return null;
    }
  };

  // Calculate arc for confidence
  const confAngle = Math.PI * confidence; // 0 to PI (semi-circle)
  const arcX = 50 - 30 * Math.cos(confAngle);
  const arcY = 95 - 10 * Math.sin(confAngle);
  const largeArcFlag = confidence > 0.5 ? 1 : 0;
  // A simple straight line bar is better suited under their feet given the 3D perspective, or an elliptical arc
  
  return (
    <svg width="100" height="120" viewBox="0 0 100 120" style={{ overflow: 'visible' }}>
      <defs>
        <radialGradient id={`halo-${archetype}`} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={color} stopOpacity={isActive ? 0.3 : 0.1} />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </radialGradient>
        <filter id="glow">
          <feGaussianBlur stdDeviation="3" result="coloredBlur"/>
          <feMerge>
            <feMergeNode in="coloredBlur"/>
            <feMergeNode in="SourceGraphic"/>
          </feMerge>
        </filter>
      </defs>

      {/* Ambient Halo */}
      <circle cx="50" cy="50" r="45" fill={`url(#halo-${archetype})`} className={isActive ? 'animate-pulse' : ''} />

      {/* HITL Glow */}
      {isHitl && (
        <circle cx="50" cy="50" r="35" fill="none" stroke="var(--amber)" strokeWidth="2" opacity="0.4" filter="url(#glow)" />
      )}

      {/* Base Chair & Silhouette */}
      <g className={isHitl ? 'hitl-lean' : ''}>
        {chair}
        {renderSilhouette()}
      </g>

      {/* Status Orb */}
      <circle
        cx="50"
        cy="12"
        r="4"
        fill={isHitl ? 'var(--amber)' : isError ? 'var(--red)' : isActive ? 'var(--green)' : 'var(--idle-color)'}
        filter={isActive || isHitl || isError ? 'url(#glow)' : ''}
        className={isActive ? 'animate-pulse' : isError ? 'animate-flash' : ''}
      />

      {/* HITL Warning Sigil */}
      {isHitl && (
        <text x="50" y="-2" fontSize="14" fill="var(--amber)" textAnchor="middle" filter="url(#glow)" style={{ animation: 'breathe-amber 2s infinite' }}>
          ⚠
        </text>
      )}

      {/* Confidence Arc below feet */}
      <path
        d="M 20 95 Q 50 105 80 95"
        fill="none"
        stroke="rgba(255,255,255,0.1)"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <path
        d="M 20 95 Q 50 105 80 95"
        fill="none"
        stroke={color}
        strokeWidth="3"
        strokeLinecap="round"
        strokeDasharray="64"
        strokeDashoffset={64 - (64 * confidence)}
        style={{ transition: 'stroke-dashoffset 0.5s ease' }}
      />
    </svg>
  );
}
