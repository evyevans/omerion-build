# R3 RICE Prioritization Formula

Last updated: 2026-06-03
Maintained by: SHAPE (r3_strategic_architect, Agent #13)

## Formula

```
RICE = (Reach × Impact × Confidence) / Effort
```

SHAPE computes RICE internally to determine `impact` and `priority_score` for each proposal.
Only the final `priority_score` and `impact` label appear in the output JSON.

---

## Variable Definitions

### Reach (1–10)
Estimated number of Omerion ICP accounts this proposal could affect within 90 days.

| Score | Meaning |
|-------|---------|
| 8–10 | Horizontal improvement affecting all service packages or all ICP personas |
| 5–7 | Affects one full service package or one major ICP persona tier |
| 3–4 | Affects a specific workflow within one package |
| 1–2 | Narrow edge case; only relevant to 1–2 specific accounts |

### Impact (1, 3, 5)
KPI movement potential if the proposal is fully implemented.

| Score | KPI benchmark |
|-------|--------------|
| 5 (massive) | Speed-to-lead: >30% reduction; conversion rate: >15% lift; owner hours: >10 hrs/week saved |
| 3 (moderate) | Speed-to-lead: 10–30% reduction; conversion: 5–15% lift; hours: 5–10 saved |
| 1 (minimal) | Marginal improvement; incremental quality gain; <5% KPI movement |

### Confidence (0.3, 0.5, 0.8, 1.0)
How strongly the signal data supports this proposal.

| Score | Evidence level |
|-------|---------------|
| 1.0 | 3+ corroborating signals across R1/R2/attribution |
| 0.8 | 2 corroborating signals |
| 0.5 | 1 signal (one R1 insight OR one OSS candidate OR one attribution report) |
| 0.3 | Hypothesis only — no loaded signal directly supports this proposal |

**0.3 confidence proposals are permitted but must be explicitly flagged** in the `problem_statement` as hypothesis-driven.

### Effort (S=1, M=2, L=4, XL=8)

| Label | Definition | Typical scope |
|-------|-----------|--------------|
| S | 1–2 days of Build agent execution | Single tool or prompt update |
| M | 1–2 weeks | New graph node or Supabase table + HITL gate |
| L | 3–4 weeks | New agent or major service package extension |
| XL | 6–8 weeks | Multi-agent workflow or full service package rebuild |

---

## Impact Label Thresholds

| RICE score | `impact` value |
|-----------|---------------|
| ≥ 10 | `"high"` |
| 5–9 | `"medium"` |
| < 5 | `"low"` |

**Hard rule:** `impact = "high"` is ONLY allowed when:
- RICE ≥ 10, AND
- A significant negative delta exists in attribution data for the targeted KPI, OR
- 3+ R1 insights in the lookback window address the same pain signal

If both conditions are not met, cap at `"medium"`.

---

## Worked Example

Proposal: "Sub-60s speed-to-lead module for DAAM"

```
Reach      = 8   (affects all revenue_leader ICP accounts using DAAM)
Impact     = 5   (>30% speed-to-lead reduction expected)
Confidence = 1.0 (3 R1 insights + 1 attribution report with negative delta)
Effort     = M=2

RICE = (8 × 5 × 1.0) / 2 = 20.0 → impact = "high"
priority_score = 20.0
```

---

## RICE and OSS Maturity Trend

When an OSS candidate appears in the `oss_block` with a `[maturity:rising]` flag,
R3 may treat this as an additional half-signal (+0.1 Confidence boost, max 1.0).
A rising maturity score indicates the library is actively maintained and adoption
velocity is increasing — reduce the risk of backing a stale dependency.
