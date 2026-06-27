# ICP Scoring — RAG Query Templates

Last updated: 2026-06-04
Maintained by: RATE (icp_scoring, Agent #6)

Semantic queries issued to the `emails` Pinecone namespace to compute the Intent sub-score.
Each query targets the pain language a contact in that archetype would write in emails.
Filter: `{"contact_id": {"$eq": str(contact_id)}}` — always scope to this specific contact.
Top-3 results; Intent = average cosine similarity of matches.

## Per-Archetype Query Strings

| Archetype | Query string |
|-----------|-------------|
| system_multiplier | "manual processes slowing down operations team need automation workflow efficiency reporting" |
| high_velocity | "losing deals slow follow-up need faster lead response pipeline velocity first touch" |
| capital_allocator | "research takes too long market data analysis decision making competitive intelligence" |
| ops_leader | "team spending hours on reporting repetitive admin tasks need to automate operations save time" |
| sme_founder | "wearing too many hats can't scale without hiring need to automate small business growth" |
| agency_owner | "client delivery slow team overwhelmed repetitive campaign management content production" |
| saas_founder | "internal ops overhead eating engineering time need to automate non-product work" |
| professional_services_owner | "research reporting consuming billable hours need to systematise knowledge work" |
| hr_talent_leader | "manual sourcing screening scheduling process talent acquisition needs automation" |
| finance_ops | "reconciliation reporting manual spreadsheet work need automated financial workflows" |
| unknown | "automation workflow efficiency time savings operations" |

## Usage Rules

- Query is run once per contact during the intent scoring step.
- If `_RagBreaker.open` is True: skip query, return Intent = 0.0 (circuit breaker active).
- If no Pinecone results returned: Intent = 0.0 (no email signal available).
- NEVER substitute the contact's actual email content for the query string — query must be archetype-driven so scores are comparable across contacts.
