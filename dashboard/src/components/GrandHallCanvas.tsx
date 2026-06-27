/* ══════════════════════════════════════════════════════════════════
   ChamberEnvironment — Photorealistic Parliament Chamber
   Strategy: Photorealistic PNG base layer (chamber-bg.png) +
   SVG overlay layer for interactive light, depth, and spatial UI.
   Pure presentation layer — zero logic, zero state.
   ══════════════════════════════════════════════════════════════════ */

interface Props {
  width: number;
  height: number;
  cx: number;   // table center X
  cy: number;   // table center Y
}

export function GrandHallCanvas({ width: W, height: H, cx, cy }: Props) {
  // The skylight in the image is at the top-center
  // The marble floor center maps to roughly the center of the image
  // We overlay a volumetric skylight shaft and cinematic vignette on top

  return (
    <>
      {/* ══════════════════════════════════════════════════════════ */}
      {/* BASE LAYER: Photorealistic Parliament Chamber Image       */}
      {/* ══════════════════════════════════════════════════════════ */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          zIndex: 0,
          backgroundImage: 'url(/chamber-bg.png)',
          backgroundSize: 'cover',
          backgroundPosition: 'center 30%',
          backgroundRepeat: 'no-repeat',
          // Slight desaturation to make agents pop against environment
          filter: 'saturate(0.88) brightness(0.78)',
        }}
      />

      {/* ══════════════════════════════════════════════════════════ */}
      {/* SVG OVERLAY: Light shafts, vignettes, atmosphere          */}
      {/* ══════════════════════════════════════════════════════════ */}
      <svg
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
          zIndex: 1,
        }}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid slice"
      >
        <defs>
          {/* ── Volumetric skylight shaft — angled natural light ── */}
          <radialGradient id="skylight-core" cx="50%" cy="0%" r="55%">
            <stop offset="0%"  stopColor="rgba(255,255,248,0.22)" />
            <stop offset="35%" stopColor="rgba(240,230,200,0.10)" />
            <stop offset="70%" stopColor="rgba(210,195,160,0.04)" />
            <stop offset="100%" stopColor="rgba(180,160,120,0)" />
          </radialGradient>

          {/* ── Center floor illumination (where skylight hits) ── */}
          <radialGradient id="floor-light" cx="50%" cy="55%" r="40%">
            <stop offset="0%"  stopColor="rgba(255,255,248,0.14)" />
            <stop offset="40%" stopColor="rgba(240,225,190,0.06)" />
            <stop offset="100%" stopColor="rgba(200,180,140,0)" />
          </radialGradient>

          {/* ── Deep cinematic vignette (chiaroscuro) ── */}
          <radialGradient id="vignette-main" cx="50%" cy="50%" r="65%">
            <stop offset="0%"  stopColor="rgba(0,0,0,0)" />
            <stop offset="55%" stopColor="rgba(0,0,0,0)" />
            <stop offset="78%" stopColor="rgba(0,0,0,0.35)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0.82)" />
          </radialGradient>

          {/* ── Top center vignette — draws eye down to table ── */}
          <radialGradient id="vignette-top" cx="50%" cy="0%" r="60%">
            <stop offset="0%"  stopColor="rgba(0,0,0,0)" />
            <stop offset="45%" stopColor="rgba(0,0,0,0)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0.55)" />
          </radialGradient>

          {/* ── Bottom darkness — table's base falls into shadow ── */}
          <linearGradient id="bottom-shadow" x1="0" y1="0" x2="0" y2="1">
            <stop offset="60%" stopColor="rgba(0,0,0,0)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0.6)" />
          </linearGradient>

          {/* ── Atmospheric haze (depth perception) ── */}
          <radialGradient id="depth-haze" cx="50%" cy="20%" r="45%">
            <stop offset="0%"  stopColor="rgba(30,22,10,0)" />
            <stop offset="60%" stopColor="rgba(10,8,4,0)" />
            <stop offset="100%" stopColor="rgba(6,4,2,0.4)" />
          </radialGradient>

          {/* ── Table area localized light glow (skylight pools here) ── */}
          <radialGradient id="table-pool" cx="50%" cy="50%" r="50%">
            <stop offset="0%"  stopColor="rgba(255,250,235,0.08)" />
            <stop offset="60%" stopColor="rgba(230,220,190,0.03)" />
            <stop offset="100%" stopColor="rgba(200,190,160,0)" />
          </radialGradient>

          {/* Volumetric shaft beam filter */}
          <filter id="shaft-blur" x="-20%" y="-5%" width="140%" height="120%">
            <feGaussianBlur stdDeviation="18 8" />
          </filter>
          <filter id="soft-blur">
            <feGaussianBlur stdDeviation="6" />
          </filter>
          {/* Dust mote glowing filter */}
          <filter id="dust-glow">
            <feGaussianBlur stdDeviation="1.5" result="blur" />
            <feComposite in="SourceGraphic" in2="blur" operator="over" />
          </filter>
        </defs>

        {/* ═══════════════════════════════════════════════════════ */}
        {/* Floating Particulate Matter (Dust Motes)                */}
        {/* Animated subtly within the volumetric light shaft       */}
        {/* ═══════════════════════════════════════════════════════ */}
        <style>{`
          @keyframes mote-float-1 {
            0% { transform: translate(0, 0) scale(1); opacity: 0; }
            20% { opacity: 0.8; }
            80% { opacity: 0.8; }
            100% { transform: translate(40px, -60px) scale(1.5); opacity: 0; }
          }
          @keyframes mote-float-2 {
            0% { transform: translate(0, 0) scale(0.8); opacity: 0; }
            20% { opacity: 0.6; }
            80% { opacity: 0.6; }
            100% { transform: translate(-30px, -80px) scale(1.2); opacity: 0; }
          }
          @keyframes mote-float-3 {
            0% { transform: translate(0, 0) scale(1.2); opacity: 0; }
            20% { opacity: 0.5; }
            80% { opacity: 0.5; }
            100% { transform: translate(20px, -100px) scale(0.9); opacity: 0; }
          }
          .mote-1 { animation: mote-float-1 12s ease-in-out infinite; }
          .mote-2 { animation: mote-float-2 15s ease-in-out infinite; }
          .mote-3 { animation: mote-float-3 18s ease-in-out infinite; }
        `}</style>
        
        {/* Motes container, positioned in the main light shaft */}
        <g style={{ mixBlendMode: 'screen' as React.CSSProperties['mixBlendMode'] }} opacity="0.4" filter="url(#dust-glow)">
          {Array.from({ length: 15 }).map((_, i) => {
            // Random positions within the central light shaft area
            const startX = W * 0.45 + (Math.random() * W * 0.1);
            const startY = cy * 0.4 + (Math.random() * cy * 0.5);
            const animClass = ['mote-1', 'mote-2', 'mote-3'][i % 3];
            const delay = `-${Math.random() * 20}s`;
            
            return (
              <circle
                key={i}
                cx={startX} cy={startY} r={Math.random() * 1.5 + 0.5}
                fill="rgba(255,248,220,0.8)"
                className={animClass}
                style={{ animationDelay: delay }}
              />
            );
          })}
        </g>

        {/* ═══════════════════════════════════════════════════════ */}
        {/* Volumetric skylight shaft                              */}
        {/* The skylight is at the image top-center, shaft falls   */}
        {/* down toward the marble floor center.                   */}
        {/* ═══════════════════════════════════════════════════════ */}
        <g style={{ mixBlendMode: 'screen' as React.CSSProperties['mixBlendMode'] }}>
          {/* Primary shaft cone */}
          <path
            d={`M ${W * 0.42} 0 L ${W * 0.31} ${cy} L ${W * 0.69} ${cy} L ${W * 0.58} 0 Z`}
            fill="url(#skylight-core)"
            filter="url(#shaft-blur)"
            opacity="0.75"
          />
          {/* Secondary narrower bright core */}
          <path
            d={`M ${W * 0.465} 0 L ${W * 0.39} ${cy * 0.85} L ${W * 0.61} ${cy * 0.85} L ${W * 0.535} 0 Z`}
            fill="rgba(255,255,250,0.08)"
            filter="url(#shaft-blur)"
          />
        </g>

        {/* ═══════════════════════════════════════════════════════ */}
        {/* Where skylight hits the floor — center illumination    */}
        {/* ═══════════════════════════════════════════════════════ */}
        <ellipse
          cx={cx} cy={cy}
          rx={W * 0.32} ry={H * 0.22}
          fill="url(#floor-light)"
          style={{ mixBlendMode: 'screen' as React.CSSProperties['mixBlendMode'] }}
        />

        {/* Localized table area glow */}
        <ellipse
          cx={cx} cy={cy}
          rx={W * 0.22} ry={H * 0.16}
          fill="url(#table-pool)"
          style={{ mixBlendMode: 'screen' as React.CSSProperties['mixBlendMode'] }}
        />

        {/* ═══════════════════════════════════════════════════════ */}
        {/* Chiaroscuro vignettes — cinematic depth                */}
        {/* ═══════════════════════════════════════════════════════ */}
        {/* Main radial vignette */}
        <rect width={W} height={H} fill="url(#vignette-main)" />

        {/* Top darkening — makes skylight appear to come from above */}
        <rect width={W} height={H} fill="url(#vignette-top)" />

        {/* Bottom shadow — seating tiers fade into darkness */}
        <rect width={W} height={H} fill="url(#bottom-shadow)" />

        {/* Atmospheric depth haze on far background */}
        <rect width={W} height={H} fill="url(#depth-haze)" />

        {/* Side columns darkening (match photo's deep column shadows) */}
        <linearGradient id="col-shadow-l" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="rgba(0,0,0,0.55)" />
          <stop offset="100%" stopColor="rgba(0,0,0,0)" />
        </linearGradient>
        <linearGradient id="col-shadow-r" x1="1" y1="0" x2="0" y2="0">
          <stop offset="0%" stopColor="rgba(0,0,0,0.55)" />
          <stop offset="100%" stopColor="rgba(0,0,0,0)" />
        </linearGradient>
        <rect width={W * 0.2} height={H} fill="url(#col-shadow-l)" />
        <rect x={W * 0.8} width={W * 0.2} height={H} fill="url(#col-shadow-r)" />

        {/* ═══════════════════════════════════════════════════════ */}
        {/* Subtle warm ambient from the skylight dome rim          */}
        {/* ═══════════════════════════════════════════════════════ */}
        <circle
          cx={W * 0.5}
          cy={H * 0.04}
          r={W * 0.28}
          fill="none"
          stroke="rgba(220,200,155,0.06)"
          strokeWidth={W * 0.08}
          style={{ filter: 'blur(20px)' }}
        />
      </svg>
    </>
  );
}
