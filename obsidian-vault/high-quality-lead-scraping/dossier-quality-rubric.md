# Dossier Quality Rubric

Last updated: 2026-06-03
Maintained by: SOURCE (high_quality_lead_scraping, Agent #2)

## Confidence Bands

Confidence is a float in [0.0, 1.0] synthesised by the cognition loop.

| Band | Range | Meaning | Action |
|------|-------|---------|--------|
| Elite | 0.90–1.00 | Direct evidence from company's own public communications (case studies, job posts, founder interviews). No inference. | Write to Pinecone with confidence_band="elite". Always gate through G2 HITL. |
| Good | 0.60–0.89 | Strong inference from multiple corroborating sources. At least one first-person signal. | Write to Pinecone with confidence_band="good". Gate through G2 HITL. |
| Weak | 0.30–0.59 | Mostly inferred from industry context or weak signals. | Do NOT write to Pinecone. Present in HITL card with explicit weak-confidence warning. |
| Discard | 0.00–0.29 | Insufficient evidence. Cannot validate pain signals. | Skip dossier entirely. Increment skipped_low_quality counter. Do not gate. |

## Quality Flags

Quality flags are set by the cognition loop and surfaced in the HITL card.

| Flag | Trigger | Founder action required |
|------|---------|------------------------|
| `low_source_count` | Fewer than 2 distinct URLs in source_urls | Verify manually before approving |
| `no_first_person_signal` | No direct quote or founder statement found | Lower confidence by 0.15 |
| `scraped_only_homepage` | All sources are the company's own homepage | High fabrication risk — scrutinise pain signals |
| `linkedin_blocked` | LinkedIn URL returned 403 or empty | Missing professional context |
| `high_dedup_similarity` | Cosine similarity 0.90–0.95 to existing dossier | Possible duplicate account — confirm before publishing |

## Disqualification Flags

If any of these are present, the dossier is REJECTED regardless of confidence.

| Flag | Trigger |
|------|---------|
| `no_clear_decision_maker` | Cannot identify a named person with buying authority |
| `consumer_business` | Account sells B2C — outside Omerion's ICP |
| `below_min_team_size` | Team size confirmed < 3 |
| `public_company` | Account is publicly listed — not Omerion's target market |
