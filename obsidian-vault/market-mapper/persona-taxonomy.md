# Market Mapper Persona Taxonomy

Last updated: 2026-06-03
Maintained by: MAP (market_mapper, Agent #1)

This taxonomy defines the 9 persona segments that MAP assigns to discovered accounts.
The classify node issues one Haiku call per account and expects exactly one token from this list.

## Segment Definitions

| Token | Label | Target Role | ICP Priority |
|-------|-------|-------------|-------------|
| `ops_leader` | Operations Leader | Head of Ops, COO, Chief of Staff, Operations Manager | Tier 1 |
| `revenue_leader` | Revenue Leader | VP Sales, Director of Growth, Revenue Lead, CRO | Tier 1 |
| `sme_founder` | SME Founder | Founder/CEO of 1–50 person services business | Tier 1 |
| `agency_owner` | Agency Owner | Founder/owner of marketing, creative, or consulting agency | Tier 2 |
| `saas_founder` | SaaS Founder | Technical founder of a software product company | Tier 2 |
| `professional_services_owner` | Prof. Services Owner | Owner of accounting, legal, recruiting, or advisory firm | Tier 2 |
| `ecommerce_operator` | Ecommerce Operator | Founder/GM of an online or hybrid retail business | Tier 3 |
| `hr_talent_leader` | HR / Talent Leader | VP People, Head of Talent, HR Director | Tier 3 |
| `finance_ops` | Finance Ops | CFO, Finance Manager, Controller | Tier 3 |
| `unknown` | Unknown | Cannot be determined from available signals | Unranked |

## ICP Weight Rationale

Tier 1 personas have the highest willingness-to-pay for automation and the clearest pain points:
- `ops_leader` — directly owns process inefficiency; buyer and champion for DAAM/ASAP
- `revenue_leader` — directly owns pipeline velocity; buyer for CAPA/REMI
- `sme_founder` — sole decision maker; fastest sales cycle; highest urgency

Tier 2 personas are good ICP but require more discovery to identify fit:
- `agency_owner` — high automation ROI but fragmented tooling; need white-label clarity
- `saas_founder` — technical sophistication; longer eval cycle; good for ASAP
- `professional_services_owner` — compliance-sensitive; slower adoption; good for DAAM

Tier 3 personas are possible but lower priority:
- `ecommerce_operator` — volume-driven; may want REMI but different pain pattern
- `hr_talent_leader` — narrow use case within Omerion's current packages
- `finance_ops` — risk-averse; long procurement cycles

## Qualification Rules

An account qualifies (qualifies=True) if it has BOTH:
1. `volume_estimate >= 50` (or no volume data extracted — benefit of the doubt)
2. `team_size >= 3` (or no team data extracted — benefit of the doubt)

Accounts with structured data below threshold are skipped (`accounts_skipped_threshold` counter).
