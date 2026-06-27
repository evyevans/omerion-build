/* ══════════════════════════════════════════════════════════════════
   SpendCalendar — The Sanctum Telemetry Board
   Dark brushed-obsidian background, dark glassmorphic plates,
   amber-glow illumination for active API spend tiles.
   Physical, high-end hardware telemetry board aesthetic.
   ══════════════════════════════════════════════════════════════════ */

import { useMemo, useState } from 'react';
import type { ActivityEvent } from '../types';

interface Props {
  activity: ActivityEvent[];
}

export function SpendCalendar({ activity }: Props) {
  const [selectedDateStr, setSelectedDateStr] = useState<string | null>(null);

  // Aggregate spend
  const financials = useMemo(() => {
    const todayNow = new Date();
    
    // 1. Calculate daily spend map for the heatmap
    const dailySpend = new Map<string, number>();
    activity.forEach(e => {
      const cost = e.costUsd || 0;
      if (cost <= 0) return;
      const dateStr = e.timestamp.toISOString().split('T')[0];
      dailySpend.set(dateStr, (dailySpend.get(dateStr) || 0) + cost);
    });

    // 2. Generate calendar days for the grid (always relative to todayNow so it's a fixed last-30-days window)
    const calendarDays = [];
    for (let i = 29; i >= 0; i--) {
      const d = new Date(todayNow.getTime() - i * 86400000);
      const dateStr = d.toISOString().split('T')[0];
      calendarDays.push({
        date: d,
        dateStr,
        spend: dailySpend.get(dateStr) || 0
      });
    }

    // 3. Determine anchorDate for rolling metrics
    let anchorDate = todayNow;
    if (selectedDateStr) {
      const selectedDay = calendarDays.find(day => day.dateStr === selectedDateStr);
      if (selectedDay) {
        const isToday = selectedDay.date.toDateString() === todayNow.toDateString();
        anchorDate = isToday
          ? todayNow
          : new Date(selectedDay.date.getFullYear(), selectedDay.date.getMonth(), selectedDay.date.getDate(), 23, 59, 59, 999);
      }
    }

    // 4. Calculate metrics relative to anchorDate
    const metrics = {
      totalSpend: 0,
      perHour: 0,
      perDay: 0,
      perWeek: 0,
      perMonth: 0,
    };

    activity.forEach(e => {
      const cost = e.costUsd || 0;
      if (cost <= 0) return;

      metrics.totalSpend += cost;

      const diffMs = anchorDate.getTime() - e.timestamp.getTime();
      if (diffMs < 0) return; // Future event relative to selected anchor date

      const diffHours = diffMs / (1000 * 60 * 60);
      const diffDays = diffMs / (1000 * 60 * 60 * 24);

      if (diffHours <= 1) metrics.perHour += cost;
      if (diffDays <= 1) metrics.perDay += cost;
      if (diffDays <= 7) metrics.perWeek += cost;
      if (diffDays <= 30) metrics.perMonth += cost;
    });

    return { ...metrics, calendarDays, anchorDate };
  }, [activity, selectedDateStr]);

  // Obsidian brushed-stone texture via CSS noise pattern
  const obsidianBg = `
    radial-gradient(ellipse at 50% 0%, rgba(28,22,12,0.95) 0%, rgba(10,8,4,1) 60%)
  `;

  return (
    <div
      id="spend-calendar-view"
      style={{
        width: '100%', height: '100%', overflow: 'auto',
        background: '#0A0805',
        backgroundImage: obsidianBg,
        padding: '40px 48px',
        fontFamily: 'var(--font-sans)',
        position: 'relative',
      }}
    >
      <style>{`
        /* Brushed obsidian noise texture */
        #spend-calendar-view::before {
          content: '';
          position: fixed;
          inset: 0;
          background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='400'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75 0.2' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='400' height='400' filter='url(%23n)' opacity='0.025'/%3E%3C/svg%3E");
          pointer-events: none;
          z-index: 0;
          mix-blend-mode: overlay;
        }

        @keyframes telemetry-pulse {
          0% { transform: scale(0.9); opacity: 0.6; box-shadow: 0 0 4px #00C896; }
          50% { transform: scale(1.1); opacity: 1; box-shadow: 0 0 12px #00C896, 0 0 24px rgba(0,200,150,0.4); }
          100% { transform: scale(0.9); opacity: 0.6; box-shadow: 0 0 4px #00C896; }
        }

        @keyframes tile-breathe {
          0%, 100% { opacity: 0.85; filter: brightness(1); }
          50% { opacity: 1; filter: brightness(1.2); }
        }

        .heatmap-tile {
          transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1) !important;
        }
        .heatmap-tile:hover {
          transform: translateY(-2px) scale(1.02);
          box-shadow: 0 12px 32px rgba(0,0,0,0.8), inset 0 1px 0 rgba(212,175,55,0.2) !important;
          border-color: rgba(212,175,55,0.4) !important;
          z-index: 10;
        }
      `}</style>

      {/* Relative container to keep content above the pseudo-element background */}
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1400, margin: '0 auto' }}>
        
        {/* ════════════════════════════════════════════════════════════ */}
        {/* Header Row                                                 */}
        {/* ════════════════════════════════════════════════════════════ */}
        <div style={{ 
          display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
          marginBottom: 36 
        }}>
          <div>
            <h2 style={{ 
              fontSize: 26, fontWeight: 800, color: '#F5F0E8', 
              margin: '0 0 8px 0', letterSpacing: '0.04em',
              textShadow: '0 2px 8px rgba(0,0,0,0.8)'
            }}>
              Financial Operations
            </h2>
            <p style={{ margin: 0, color: 'rgba(235,220,195,0.6)', fontSize: 13, letterSpacing: '0.02em' }}>
              Pristine, non-hallucinated API cost aggregation directly from the backend event bus.
            </p>
          </div>

          {/* ── Selection Status Badge (Dark Glass) ── */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            background: 'rgba(14, 11, 6, 0.45)',
            backdropFilter: 'blur(32px) saturate(1.4)',
            WebkitBackdropFilter: 'blur(32px) saturate(1.4)',
            border: selectedDateStr ? '1px solid rgba(255, 170, 0, 0.3)' : '1px solid rgba(0, 200, 150, 0.3)',
            borderTop: selectedDateStr ? '1px solid rgba(255, 170, 0, 0.5)' : '1px solid rgba(0, 200, 150, 0.5)',
            padding: '8px 20px',
            borderRadius: 100,
            fontFamily: 'var(--font-sans)',
            fontSize: 11,
            fontWeight: 700,
            boxShadow: '0 8px 24px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.05)',
          }}>
            {selectedDateStr ? (
              <>
                {/* Amber status light */}
                <span style={{ 
                  width: 7, height: 7, borderRadius: '50%', backgroundColor: '#FFAA00',
                  boxShadow: '0 0 8px #FFAA00, 0 0 16px rgba(255,170,0,0.5)'
                }} />
                <span style={{ color: '#FFD166', letterSpacing: '0.06em', textShadow: '0 1px 4px rgba(0,0,0,0.8)' }}>
                  HISTORICAL: {new Date(selectedDateStr + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).toUpperCase()}
                </span>
                <button 
                  onClick={() => setSelectedDateStr(null)}
                  style={{
                    background: 'none', border: 'none', color: '#FFD166', cursor: 'pointer',
                    fontSize: 14, fontWeight: 900, padding: '0 0 0 6px', display: 'flex', alignItems: 'center',
                    transition: 'color 0.2s, text-shadow 0.2s',
                  }}
                  onMouseEnter={e => {
                    e.currentTarget.style.color = '#FFF';
                    e.currentTarget.style.textShadow = '0 0 8px #FFAA00';
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.color = '#FFD166';
                    e.currentTarget.style.textShadow = 'none';
                  }}
                  title="Reset to live metrics"
                >
                  ✕
                </button>
              </>
            ) : (
              <>
                {/* Emerald status light pulsing */}
                <span style={{ 
                  width: 7, height: 7, borderRadius: '50%', backgroundColor: '#00C896',
                  animation: 'telemetry-pulse 2.5s ease-in-out infinite'
                }} />
                <span style={{ color: '#00E6A8', letterSpacing: '0.06em', textShadow: '0 1px 4px rgba(0,0,0,0.8)' }}>
                  LIVE REAL-TIME METRICS
                </span>
              </>
            )}
          </div>
        </div>

        {/* ════════════════════════════════════════════════════════════ */}
        {/* Metric Cards (Dark Glassmorphic Plates)                    */}
        {/* ════════════════════════════════════════════════════════════ */}
        <div style={{ 
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 56 
        }}>
          <MetricCard title="Past Hour" value={financials.perHour} isHistorical={!!selectedDateStr} />
          <MetricCard title="Past 24 Hours" value={financials.perDay} isHistorical={!!selectedDateStr} />
          <MetricCard title="Past 7 Days" value={financials.perWeek} isHistorical={!!selectedDateStr} />
          <MetricCard title="Past 30 Days" value={financials.perMonth} isHistorical={!!selectedDateStr} />
        </div>

        {/* ════════════════════════════════════════════════════════════ */}
        {/* 30-Day Heatmap (Physical Glass Tiles)                      */}
        {/* ════════════════════════════════════════════════════════════ */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20 }}>
          <h3 style={{ 
            fontSize: 14, fontWeight: 700, color: 'rgba(212,175,55,0.7)', 
            margin: 0, textTransform: 'uppercase', letterSpacing: '0.15em',
            textShadow: '0 1px 4px rgba(0,0,0,0.8)'
          }}>
            30-Day Telemetry Heatmap
          </h3>
          <div style={{ height: 1, flex: 1, background: 'linear-gradient(90deg, rgba(212,175,55,0.2) 0%, transparent 100%)' }} />
        </div>
        
        <div style={{
          // Precise 2px gutters
          display: 'grid', gridTemplateColumns: 'repeat(10, 1fr)', gap: 2,
          // Encased in a subtle dark rim
          padding: 2, background: 'rgba(0,0,0,0.4)', borderRadius: 12,
          border: '1px solid rgba(255,255,255,0.03)',
          boxShadow: 'inset 0 4px 12px rgba(0,0,0,0.6)',
        }}>
          {financials.calendarDays.map((day) => {
            // High-spend days pulse with deep volumetric amber/gold core
            // Low-spend days emit faint dim glow
            const isActive = day.spend > 0;
            let intensity = 0;
            if (isActive) {
              // Normalize intensity between 0.2 and 1.0 based on spend magnitude
              intensity = 0.2 + Math.min(0.8, day.spend / 2.0);
            }

            const isSelected = selectedDateStr === day.dateStr;

            // Base tile background
            let bg = 'rgba(18, 14, 10, 0.6)';
            let boxShadow = 'none';
            let border = '1px solid rgba(255,255,255,0.02)';
            let animation = 'none';
            let textShadow = 'none';

            if (isSelected) {
              // Selected state — bright amber highlight
              bg = 'rgba(255, 170, 0, 0.15)';
              border = '1px solid rgba(255, 170, 0, 0.6)';
              boxShadow = 'inset 0 0 20px rgba(255,170,0,0.2), 0 0 12px rgba(255,170,0,0.3)';
              textShadow = '0 0 12px rgba(255,170,0,0.8)';
            } else if (isActive) {
              // Active spend — internal illumination (amber/gold)
              bg = `rgba(212, 175, 55, ${intensity * 0.15})`;
              border = `1px solid rgba(212, 175, 55, ${intensity * 0.4})`;
              boxShadow = `inset 0 0 ${20 * intensity}px rgba(212,175,55,${intensity * 0.3})`;
              // High intensity gets subtle breathing
              if (intensity > 0.6) {
                animation = 'tile-breathe 4s ease-in-out infinite';
              }
              textShadow = `0 0 ${12 * intensity}px rgba(212,175,55,${intensity * 0.9})`;
            }

            return (
              <div 
                key={day.dateStr} 
                className="heatmap-tile"
                onClick={() => setSelectedDateStr(isSelected ? null : day.dateStr)}
                style={{
                  background: bg,
                  border: border,
                  // Beveled glass look
                  borderTop: isSelected ? border : `1px solid rgba(255,255,255,${isActive ? intensity * 0.2 : 0.05})`,
                  borderRadius: 6,
                  padding: '12px 10px',
                  display: 'flex', flexDirection: 'column',
                  minHeight: 80,
                  cursor: 'pointer',
                  boxShadow: boxShadow,
                  animation: animation,
                  position: 'relative',
                }}
              >
                {/* Date Label */}
                <span style={{ 
                  fontSize: 10, 
                  fontWeight: isSelected ? 800 : (isActive ? 700 : 600), 
                  color: isSelected ? '#FFD166' : (isActive ? `rgba(255,240,200,${0.6 + intensity * 0.4})` : 'rgba(255,255,255,0.25)'),
                  letterSpacing: '0.04em',
                  textShadow: isSelected || isActive ? textShadow : '0 1px 2px rgba(0,0,0,0.8)'
                }}>
                  {day.date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </span>

                <div style={{ flex: 1 }} />
                
                {/* Spend Value */}
                <span style={{ 
                  fontSize: 15, fontWeight: 800, 
                  color: isSelected ? '#FFD166' : (isActive ? '#F5E1A4' : 'rgba(255,255,255,0.15)'),
                  letterSpacing: '0.02em',
                  textShadow: isSelected || isActive ? textShadow : '0 1px 2px rgba(0,0,0,0.8)'
                }}>
                  ${day.spend.toFixed(2)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════
   MetricCard — Elevated Dark Glassmorphic Plate
   ══════════════════════════════════════════════════════════════════ */
function MetricCard({ title, value, isHistorical }: { title: string, value: number, isHistorical?: boolean }) {
  const isZero = value === 0;

  return (
    <div style={{
      background: isHistorical ? 'rgba(255, 170, 0, 0.05)' : 'rgba(14, 11, 6, 0.45)',
      backdropFilter: 'blur(32px) saturate(1.4)',
      WebkitBackdropFilter: 'blur(32px) saturate(1.4)',
      border: isHistorical ? '1px solid rgba(255, 170, 0, 0.3)' : '1px solid rgba(212, 175, 55, 0.15)',
      // Specular top edge catching skylight
      borderTop: isHistorical ? '1px solid rgba(255, 170, 0, 0.5)' : '1px solid rgba(255, 248, 220, 0.28)',
      borderRadius: 12,
      padding: '24px 28px',
      transition: 'all 0.3s ease',
      boxShadow: isHistorical 
        ? '0 12px 32px rgba(0,0,0,0.6), inset 0 0 20px rgba(255,170,0,0.05)' 
        : '0 12px 32px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.05)',
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ 
        fontSize: 10, fontWeight: 700, 
        color: isHistorical ? '#FFD166' : 'rgba(212, 175, 55, 0.65)', 
        textTransform: 'uppercase', letterSpacing: '0.15em', marginBottom: 12,
        textShadow: '0 1px 4px rgba(0,0,0,0.8)'
      }}>
        {title} {isHistorical && '• HISTORICAL'}
      </div>
      
      {/* Values subtly illuminated from behind */}
      <div style={{ 
        fontSize: 36, fontWeight: 800, 
        color: isHistorical ? '#FFD166' : (isZero ? 'rgba(255,255,255,0.2)' : '#F5E1A4'), 
        letterSpacing: '-0.02em',
        fontFamily: 'var(--font-mono)',
        textShadow: isHistorical 
          ? '0 0 16px rgba(255,170,0,0.6)' 
          : (isZero ? '0 1px 2px rgba(0,0,0,0.8)' : '0 0 16px rgba(212,175,55,0.4)')
      }}>
        ${value.toFixed(2)}
      </div>
    </div>
  );
}
