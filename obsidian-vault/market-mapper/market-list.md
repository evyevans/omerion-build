# Market Mapper Active Markets

Last updated: 2026-06-03
Source of truth: agents.yaml → market_mapper.target_markets
Maintained by: MAP (market_mapper, Agent #1)

## Active Markets

Markets listed here are the canonical source for MAP's weekly discovery run.
The config in agents.yaml is authoritative — this file is editorial context.

| Market Segment | ICP Match | Why This Market | Typical Persona |
|---------------|-----------|----------------|----------------|
| B2B SaaS | High | High automation ROI, tech-forward decision makers | saas_founder, ops_leader |
| Digital Marketing Agencies | High | Process-heavy, fragmented tooling, repeatable client work | agency_owner, revenue_leader |
| Professional Services | Medium | Admin overhead is significant; compliance-aware | professional_services_owner, finance_ops |
| E-commerce Operators | Medium | High volume ops, inventory/fulfilment pain | ecommerce_operator, ops_leader |
| Recruitment & Staffing | Medium | Manual sourcing; strong CAPA fit | hr_talent_leader, revenue_leader |

## Qualification Thresholds

| Signal | Minimum | Config key |
|--------|---------|-----------|
| Volume estimate (clients/accounts) | 50 | `min_volume_threshold` |
| Team size | 3 | `min_team_size` |
| Final score | Not gated (all qualifying accounts pass to DB) | — |

## Adding a New Market

1. Add the segment name string to `agents.yaml → market_mapper.target_markets`
2. Add a row to the Active Markets table above with ICP match rating and rationale
3. If the new market introduces a new persona type, update persona-taxonomy.md

## SerpAPI Query Strategy

MAP issues 4 queries per market segment (see `scrape_market` in tools.py):
1. `{market} operations automation software company`
2. `{market} growth-stage B2B startup AI automation`
3. `{market} digital marketing agency AI tools`
4. `{market} professional services consulting firm technology`

Results are deduplicated by domain before classification. Noise domains filtered:
google.com, yelp.com, indeed.com, glassdoor.com.
