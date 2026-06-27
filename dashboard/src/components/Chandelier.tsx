/* ══════════════════════════════════════════════════════════════════
   Chandelier — Tiered Crystal Chandelier
   Pure SVG. Warm golden bloom. Imperceptible 8s flicker cycle.
   Suspended from the grand hall ceiling at the table center.
   ══════════════════════════════════════════════════════════════════ */

interface Props {
  cx: number;    // Center X (same as table center)
  cy: number;    // Center Y (should be near top of viewport)
  scale?: number; // Size multiplier (default 1.0)
}

export function Chandelier({ cx, cy, scale = 1.0 }: Props) {
  const s = scale;

  // Tier radii
  const t1R = 90 * s;   // Crown tier (top)
  const t2R = 130 * s;  // Middle tier
  const t3R = 72 * s;   // Bottom tier (smaller, drips down)
  const t1Y = cy + 18 * s;
  const t2Y = cy + 52 * s;
  const t3Y = cy + 84 * s;

  // Crystal drip count per tier
  const t1Count = 16;
  const t2Count = 24;
  const t3Count = 12;

  return (
    <svg
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 2,
        overflow: 'visible',
      }}
    >
      <defs>
        {/* Central warm light orb */}
        <radialGradient id="ch-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%"   stopColor="#FFF8DC" stopOpacity="0.95" />
          <stop offset="20%"  stopColor="#F0C040" stopOpacity="0.7" />
          <stop offset="50%"  stopColor="#D4AF37" stopOpacity="0.35" />
          <stop offset="80%"  stopColor="#8B6914" stopOpacity="0.1" />
          <stop offset="100%" stopColor="#4A3800" stopOpacity="0" />
        </radialGradient>

        {/* Tier ring gradient — gold with specular highlight */}
        <linearGradient id="ch-ring-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="#FFF2CD" />
          <stop offset="30%"  stopColor="#D4AF37" />
          <stop offset="60%"  stopColor="#AA7A00" />
          <stop offset="100%" stopColor="#E6C875" />
        </linearGradient>

        {/* Crystal prism gradient */}
        <linearGradient id="ch-crystal" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor="#FFFFFF" stopOpacity="0.9" />
          <stop offset="40%"  stopColor="#FFF8DC" stopOpacity="0.7" />
          <stop offset="70%"  stopColor="#D4AF37" stopOpacity="0.5" />
          <stop offset="100%" stopColor="#AA7A00" stopOpacity="0.3" />
        </linearGradient>

        {/* Warm downward cone of light */}
        <radialGradient id="ch-cone" cx="50%" cy="0%" r="80%">
          <stop offset="0%"   stopColor="#C8960C" stopOpacity="0.22" />
          <stop offset="50%"  stopColor="#8B6914" stopOpacity="0.06" />
          <stop offset="100%" stopColor="#3A2800" stopOpacity="0" />
        </radialGradient>

        {/* Crown cap gradient */}
        <radialGradient id="ch-crown" cx="50%" cy="30%" r="70%">
          <stop offset="0%"   stopColor="#FFF2CD" />
          <stop offset="60%"  stopColor="#D4AF37" />
          <stop offset="100%" stopColor="#6B4F00" />
        </radialGradient>

        {/* Chain link gradient */}
        <linearGradient id="ch-chain" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"   stopColor="#8A6300" />
          <stop offset="50%"  stopColor="#FFF2CD" />
          <stop offset="100%" stopColor="#8A6300" />
        </linearGradient>
      </defs>

      {/* ── Suspension chain from ceiling ── */}
      <rect
        x={cx - 3 * s} y={cy - 80 * s}
        width={6 * s} height={80 * s}
        fill="url(#ch-chain)"
        rx={1}
      />
      {/* Chain link details */}
      {[0, 1, 2, 3, 4, 5].map(i => (
        <ellipse key={`link-${i}`}
          cx={cx}
          cy={cy - 70 * s + i * 12 * s}
          rx={5 * s} ry={3 * s}
          fill="none"
          stroke="url(#ch-chain)"
          strokeWidth="1.5"
        />
      ))}

      {/* ── Crown cap (top of chandelier) ── */}
      <ellipse cx={cx} cy={cy - 4 * s} rx={22 * s} ry={10 * s}
        fill="url(#ch-crown)"
        filter="drop-shadow(0 2px 6px rgba(212,175,55,0.4))"
      />
      <ellipse cx={cx} cy={cy - 4 * s} rx={22 * s} ry={10 * s}
        fill="none" stroke="rgba(255,242,205,0.6)" strokeWidth="1"
      />

      {/* ── Tier 1: Crown tier ── */}
      {/* Ring body */}
      <ellipse cx={cx} cy={t1Y} rx={t1R} ry={t1R * 0.18}
        fill="url(#ch-ring-grad)"
        stroke="rgba(255,242,205,0.5)"
        strokeWidth="0.8"
        style={{ filter: 'drop-shadow(0 2px 8px rgba(212,175,55,0.5))' }}
      />
      {/* Crystal drips */}
      {Array.from({ length: t1Count }).map((_, i) => {
        const angle = (i / t1Count) * 2 * Math.PI;
        const dx = t1R * Math.cos(angle);
        const dy = t1R * 0.18 * Math.sin(angle);
        const dripH = (8 + Math.random() * 8) * s;
        return (
          <g key={`t1c-${i}`}>
            <polygon
              points={`${cx + dx - 2 * s},${t1Y + dy} ${cx + dx + 2 * s},${t1Y + dy} ${cx + dx},${t1Y + dy + dripH}`}
              fill="url(#ch-crystal)"
              opacity="0.85"
            />
          </g>
        );
      })}

      {/* Vertical rods from tier 1 to tier 2 */}
      {[0, 1, 2, 3, 4, 5, 6, 7].map(i => {
        const angle = (i / 8) * 2 * Math.PI;
        return (
          <line key={`rod-${i}`}
            x1={cx + t1R * 0.75 * Math.cos(angle)}
            y1={t1Y + t1R * 0.18 * Math.sin(angle)}
            x2={cx + t2R * 0.8 * Math.cos(angle)}
            y2={t2Y + t2R * 0.15 * Math.sin(angle)}
            stroke="url(#ch-ring-grad)"
            strokeWidth="1.5"
            opacity="0.7"
          />
        );
      })}

      {/* ── Tier 2: Middle tier (largest) ── */}
      <ellipse cx={cx} cy={t2Y} rx={t2R} ry={t2R * 0.15}
        fill="url(#ch-ring-grad)"
        stroke="rgba(255,242,205,0.5)"
        strokeWidth="1"
        style={{ filter: 'drop-shadow(0 2px 12px rgba(212,175,55,0.6))' }}
      />
      {/* Crystal drips — tier 2 (longer drips) */}
      {Array.from({ length: t2Count }).map((_, i) => {
        const angle = (i / t2Count) * 2 * Math.PI;
        const dx = t2R * Math.cos(angle);
        const dy = t2R * 0.15 * Math.sin(angle);
        const dripH = (12 + (i % 3) * 6) * s;
        const dripW = (3 + (i % 2)) * s;
        return (
          <polygon
            key={`t2c-${i}`}
            points={`${cx + dx - dripW},${t2Y + dy} ${cx + dx + dripW},${t2Y + dy} ${cx + dx},${t2Y + dy + dripH}`}
            fill="url(#ch-crystal)"
            opacity="0.88"
          />
        );
      })}

      {/* Vertical rods tier 2 → tier 3 */}
      {[0, 1, 2, 3, 4, 5].map(i => {
        const angle = (i / 6) * 2 * Math.PI;
        return (
          <line key={`rod2-${i}`}
            x1={cx + t2R * 0.55 * Math.cos(angle)}
            y1={t2Y + t2R * 0.15 * Math.sin(angle)}
            x2={cx + t3R * 0.85 * Math.cos(angle)}
            y2={t3Y + t3R * 0.2 * Math.sin(angle)}
            stroke="url(#ch-ring-grad)"
            strokeWidth="1"
            opacity="0.6"
          />
        );
      })}

      {/* ── Tier 3: Bottom tier ── */}
      <ellipse cx={cx} cy={t3Y} rx={t3R} ry={t3R * 0.2}
        fill="url(#ch-ring-grad)"
        stroke="rgba(255,242,205,0.5)"
        strokeWidth="0.8"
        style={{ filter: 'drop-shadow(0 2px 10px rgba(212,175,55,0.5))' }}
      />
      {Array.from({ length: t3Count }).map((_, i) => {
        const angle = (i / t3Count) * 2 * Math.PI;
        const dx = t3R * Math.cos(angle);
        const dy = t3R * 0.2 * Math.sin(angle);
        const dripH = (14 + (i % 4) * 5) * s;
        return (
          <polygon
            key={`t3c-${i}`}
            points={`${cx + dx - 2.5 * s},${t3Y + dy} ${cx + dx + 2.5 * s},${t3Y + dy} ${cx + dx},${t3Y + dy + dripH}`}
            fill="url(#ch-crystal)"
            opacity="0.9"
          />
        );
      })}

      {/* ── Central lamp body ── */}
      <ellipse cx={cx} cy={cy + 10 * s} rx={14 * s} ry={18 * s}
        fill="#1C1408"
        stroke="url(#ch-ring-grad)"
        strokeWidth="1.5"
      />

      {/* ── Core light source ── */}
      {/* Inner brilliant white core */}
      <circle cx={cx} cy={cy + 10 * s} r={8 * s}
        fill="#FFFDE0"
        style={{
          filter: 'blur(3px)',
          animation: 'chandelier-flicker 8s ease-in-out infinite',
        }}
        opacity="0.95"
      />

      {/* ── Warm bloom — chandelier's primary ambient contribution ── */}
      <ellipse
        cx={cx} cy={cy + 20 * s}
        rx={200 * s} ry={160 * s}
        fill="url(#ch-bloom)"
        style={{
          animation: 'chandelier-flicker 8s ease-in-out infinite',
        }}
      />

      {/* Downward cone of warm light */}
      <path
        d={`M ${cx - 20 * s} ${cy + 30 * s} 
            L ${cx - 280 * s} ${cy + 400 * s} 
            L ${cx + 280 * s} ${cy + 400 * s} 
            L ${cx + 20 * s} ${cy + 30 * s} Z`}
        fill="url(#ch-cone)"
        style={{ animation: 'chandelier-flicker 8s ease-in-out infinite' }}
      />

      {/* ── Specular sparkle glints ── */}
      {[
        { x: cx - t2R * 0.6, y: t2Y - 4 * s },
        { x: cx + t2R * 0.4, y: t2Y + 2 * s },
        { x: cx - t1R * 0.3, y: t1Y - 2 * s },
        { x: cx + t1R * 0.7, y: t1Y + 1 * s },
        { x: cx - t3R * 0.5, y: t3Y - 1 * s },
        { x: cx + t3R * 0.8, y: t3Y + 3 * s },
      ].map((pt, i) => (
        <g key={`sparkle-${i}`}
          style={{ animation: `chandelier-flicker ${6 + i * 0.7}s ease-in-out infinite ${i * 0.4}s` }}>
          <line x1={pt.x - 4 * s} y1={pt.y} x2={pt.x + 4 * s} y2={pt.y}
            stroke="rgba(255,255,255,0.8)" strokeWidth="0.8" />
          <line x1={pt.x} y1={pt.y - 4 * s} x2={pt.x} y2={pt.y + 4 * s}
            stroke="rgba(255,255,255,0.8)" strokeWidth="0.8" />
          <circle cx={pt.x} cy={pt.y} r={1.5 * s}
            fill="white" opacity="0.9" />
        </g>
      ))}
    </svg>
  );
}
