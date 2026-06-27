# ICP Scoring — Segment Thresholds

Last updated: 2026-06-04
Maintained by: RATE (icp_scoring, Agent #6)

## Scoring Weights per Operator Archetype

| Archetype | Fit weight | Intent weight | Timing weight |
|-----------|-----------|--------------|--------------|
| system_multiplier | 0.55 | 0.25 | 0.20 |
| high_velocity | 0.40 | 0.45 | 0.15 |
| capital_allocator | 0.45 | 0.30 | 0.25 |

Blend formula: `final_score = Fit × w_fit + Intent × w_intent + Timing × w_timing`

## Tier Cutoffs

| Tier | Score range | Downstream action |
|------|------------|------------------|
| hot | ≥ 0.70 | Enqueue for PAIR (offer_matching) immediately |
| warm | 0.45 – 0.69 | Include in weekly digest; REACH sequences eligible |
| watchlist | 0.25 – 0.44 | Monitor; re-score on next contact activity event |
| cold | < 0.25 | No action; archive if cold for 90+ days |

## Sub-Score Definitions

**Fit (0.0–1.0):** Match of contact.persona + account.market to ideal ICP profile.
Strong Fit signals by persona:
- `ops_leader` + B2B SaaS or professional services → Fit ≥ 0.75
- `revenue_leader` + agency or high-growth startup → Fit ≥ 0.75
- `sme_founder` + any market with tech adoption signals → Fit ≥ 0.70
- `unknown` persona → Fit capped at 0.40 regardless of market

**Intent (0.0–1.0):** Semantic pain-match score from Pinecone `emails` namespace.
Computed as average cosine similarity of top-3 email vectors vs. archetype query.
If no email vectors found: Intent defaults to 0.30 (neutral, not penalised).

**Timing (0.0–1.0):** Engagement recency from contact_activity_log.
- last_activity < 7 days → 1.0
- last_activity 7–30 days → 0.70
- last_activity 30–90 days → 0.40
- last_activity > 90 days → 0.10
