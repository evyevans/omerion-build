# Healer — RSI Threshold Matrix

Last updated: 2026-06-04
Maintained by: FIX (healer, Agent #16)

RSI (Runtime Stability Index) thresholds. Healer wakes when a metric crosses
from Warning into Actionable. Escalation band = escalate regardless of loop guard status.

## p95 Latency (seconds per run)

| Band | Range | Healer action |
|------|-------|--------------|
| Healthy | < 15s | None |
| Warning | 15s – 29s | Monitor; log in weekly report |
| Actionable | 30s – 59s | Diagnose + formulate patch |
| Escalation | ≥ 60s | Escalate immediately; do not patch |

## Error Rate (% of runs in last 6h)

| Band | Range | Healer action |
|------|-------|--------------|
| Healthy | < 5% | None |
| Warning | 5% – 19% | Monitor; log in weekly report |
| Actionable | 20% – 49% | Diagnose + formulate patch |
| Escalation | ≥ 50% | Escalate immediately |

## Cost Per Run vs Ceiling

| Band | Range | Healer action |
|------|-------|--------------|
| Healthy | < 60% of ceiling | None |
| Warning | 60% – 79% of ceiling | Monitor |
| Actionable | 80% – 99% of ceiling | Recommend max_tokens / tier reduction |
| Escalation | ≥ 100% (ceiling breached) | Auditor freezes; Healer escalates only |

## Consecutive Failure Count

| Band | Count | Healer action |
|------|-------|--------------|
| Healthy | 0 – 1 | None |
| Warning | 2 | Log |
| Actionable | 3 – 4 | Diagnose |
| Escalation | ≥ 5 | Escalate; do not patch |

## Wakeup Sources

1. **DEPLOYMENT_HEALTH_FAILED event** — immediate reactive trigger; 60s SLA to begin diagnosis
2. **Nightly cron sweep** — checks all agents for Warning-band metrics; no SLA
