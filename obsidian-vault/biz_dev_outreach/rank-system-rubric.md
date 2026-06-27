# Biz Dev Outreach — Rank System Rubric

Last updated: 2026-06-04
Maintained by: SEEK (biz_dev_outreach, Agent #15)

Weighted ranking formula applied to every discovered opportunity before the application decision.

## Scoring Formula

```
rank_score = domain_match×0.40 + stack_match×0.25 + budget_score×0.15 + remote_score×0.10 + length_score×0.10
```

All sub-scores: 0.0 – 10.0. Minimum rank_score to apply: varies by platform tier (see below).

## Sub-Score Definitions

### domain_match (0–10, weight 40%)
Does the engagement require AI automation, workflow orchestration, or ops intelligence consulting?
| Score | Meaning |
|-------|---------|
| 9–10 | Direct match: AI agent build, LangGraph/RAG, automation consulting, AI SaaS product |
| 7–8 | Adjacent: data pipelines, RevOps build, process improvement consulting |
| 4–6 | Partial: general SaaS, product management, growth ops |
| 0–3 | Mismatch: pure engineering, design, finance, legal |

### stack_match (0–10, weight 25%)
Overlap with Omerion canonical stack: LangGraph, Supabase, Pinecone, Python, Claude/Anthropic, FastAPI
| Score | Overlap |
|-------|---------|
| 9–10 | 3+ canonical tools explicitly listed |
| 7–8 | 2 canonical tools listed |
| 4–6 | Adjacent tools (other vector DBs, other LLMs, PostgreSQL) |
| 0–3 | No overlap |

### budget_score (0–10, weight 15%)
| Score | Budget range |
|-------|-------------|
| 9–10 | ≥ $10k/month explicitly stated |
| 7–8 | $6k–$10k/month |
| 4–6 | $3k–$6k/month |
| 0–3 | < $3k/month OR no budget stated |

### remote_score (0–10, weight 10%)
| Score | Work arrangement |
|-------|----------------|
| 10 | Fully remote, async-first |
| 5 | Remote with occasional onsite (< monthly) |
| 0 | Onsite required |

### length_score (0–10, weight 10%)
Preferred engagement: 3–6 months.
| Score | Duration |
|-------|---------|
| 10 | 3–6 months |
| 7 | > 6 months (fine, not preferred) |
| 4 | 1–3 months |
| 0 | < 1 month |

## Platform Tier Minimum Scores

| Tier | Platforms | Min rank_score to apply |
|------|----------|------------------------|
| S | Toptal, direct referrals | 7.5 |
| A | Upwork (verified), Gun.io | 7.0 |
| B | LinkedIn Jobs, AngelList | 6.5 |

Score below minimum = `low_rank_score` HITL flag. Founder decides whether to proceed.
