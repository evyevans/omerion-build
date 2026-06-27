/* ══════════════════════════════════════════════════════════════════
   MetricsBar — Presidential Command Bar (Light Glass)
   ══════════════════════════════════════════════════════════════════ */

import { useEffect, useState } from 'react';
import type { MetricsData } from '../types';

interface Props {
  metrics: MetricsData;
  isTableView?: boolean;
}

export function MetricsBar({ metrics, isTableView = true }: Props) {
  // Since all views are now dark, we enforce the dark theme universally
  const bg = 'rgba(10, 14, 22, 0.75)';
  const borderBottom = '1px solid rgba(255, 255, 255, 0.08)';
  const boxShadow = '0 4px 24px rgba(0,0,0,0.2)';
  const textColor = '#FFFFFF';
  const breadcrumbColor = 'rgba(255, 255, 255, 0.5)';
  const dividerColor = 'rgba(255, 255, 255, 0.15)';
  const tickerColor = 'rgba(255, 255, 255, 0.6)';

  return (
    <header
      id="metrics-bar"
      style={{
        height: 52,
        background: bg,
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        borderBottom: borderBottom,
        display: 'flex',
        alignItems: 'center',
        padding: '0 24px',
        flexShrink: 0,
        position: 'relative',
        zIndex: 100,
        boxShadow: boxShadow,
        transition: 'all 0.3s ease',
      }}
    >
      {/* Omerion wordmark */}
      <div className="sans" style={{
        fontSize: 16,
        fontWeight: 700,
        color: textColor,
        letterSpacing: '0.08em',
      }}>
        OMERION
      </div>

      {/* Divider */}
      <div style={{
        width: 1, height: 16, background: dividerColor, margin: '0 16px'
      }} />

      {/* COUNCIL breadcrumb */}
      <div className="sans" style={{
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.12em',
        color: breadcrumbColor,
        textTransform: 'uppercase'
      }}>
        Council
      </div>

      <div style={{ flex: 1 }} />

      {/* Metric Pills */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <MetricPill label="AGENTS" value={`${metrics.totalAgents}`} />
        <MetricPill label="ACTIVE" value={`${metrics.activeNow}`} color="var(--green)" />
        <MetricPill label="HITL" value={`${metrics.hitlPending}`} color="var(--amber)" />
        <MetricPill label="CONFIDENCE" value={`${(metrics.systemConfidence * 100).toFixed(0)}%`} />
        <MetricPill label="SPEND" value={`$${metrics.tokenSpendToday.toFixed(2)}`} />
      </div>

      <div style={{ flex: 1 }} />

      {/* Live action ticker */}
      {metrics.lastAction && (
        <div className="sans" style={{
          fontSize: 12,
          fontWeight: 400,
          color: tickerColor,
          marginRight: 20,
          maxWidth: 350,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap'
        }}>
          <span style={{ fontWeight: 600, color: textColor }}>{metrics.lastAction.agentName}</span>{' '}
          {metrics.lastAction.action}
        </div>
      )}


    </header>
  );
}

function MetricPill({ label, value, color }: { label: string; value: string; color?: string }) {
  const bg = 'rgba(255, 255, 255, 0.05)';
  const border = '1px solid rgba(255, 255, 255, 0.08)';
  const labelColor = 'rgba(255, 255, 255, 0.4)';
  const valueColor = '#FFFFFF';

  return (
    <div style={{
      background: bg,
      border: border,
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      borderRadius: 100,
      padding: '4px 12px',
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      transition: 'all 0.2s ease',
    }}>
      {color && (
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}66` }} />
      )}
      <span className="sans" style={{ fontSize: 9, fontWeight: 700, color: labelColor, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        {label}
      </span>
      <span className="sans" style={{ fontSize: 13, fontWeight: 600, color: valueColor }}>
        {value}
      </span>
    </div>
  );
}
