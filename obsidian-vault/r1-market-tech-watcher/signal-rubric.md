# Signal Rubric — TRACK (R1 Market/Tech Watcher)

**Maintained by:** TRACK (r1_market_tech_watcher, Agent #11)  
**Last updated:** 2026-06-03  
**Purpose:** Governs how raw RSS signals are evaluated, scored, and promoted to
`rd_insights`. These are the canonical quality rules — the system prompt (TAG_SYSTEM)
extracts these rules directly from this document.

---

## Signal Quality Thresholds

A signal MUST pass ALL of the following to be tagged and written to `rd_insights`:

| Requirement | Rule |
|---|---|
| Minimum keyword matches | ≥2 keywords from `relevance_filter_keywords` in agents.yaml |
| Non-empty title | Signal must have a non-empty `title` field from the RSS entry |
| Non-empty URL | Signal must have a non-empty `link` field |
| Summary length | `raw_content` must be non-empty; empty-content entries are dropped |
| Tag validity | `impact_tag` must be one of: `daam`, `capa`, `remi`, `asap`, `internal_os` |

Signals failing any criterion are silently dropped before the LLM tagging step.

---

## Priority Assignment Rules (RICE-calibrated)

Priority is set by Haiku during tagging, not inferred from keywords alone.

### `high`
The signal is EITHER:
- **(a) A direct competitive threat:** Product launch targeting Omerion's ICP
  (ops leaders, SME founders, revenue leaders, agency owners) with >$10M funding.
  MUST force `estimated_priority = "high"` and name the competitive threat in summary.
- **(b) An immediate adoption candidate:** Tool, framework, or pattern that can
  improve a service package within 30 days.

**Decision heuristic:** Reach × Impact ≥ 7/10 on direct ICP overlap = HIGH.

### `medium`
Worth watching this quarter. Partial overlap with one service package or one ICP
persona. Early-stage development, research phase, or no confirmed funding.

### `low`
Informational context. No direct package or ICP relevance. General AI/tech industry
news without specific Omerion alignment.

**Hard rule:** NEVER assign `high` to a signal that is only broadly interesting to
the AI industry. HIGH requires direct overlap with Omerion's ICP or service packages.

---

## Disqualification Criteria

These signal types are dropped BEFORE the keyword filter. The `is_relevant()` gate
handles keyword filtering; these are editorial disqualifiers:

| Type | Why dropped |
|---|---|
| Press releases with no substantive content | Usually just product announcements without operational details |
| Job posting aggregators | No signal relevance to service packages |
| Paywalled content with <50 word preview | Insufficient context for accurate tagging |
| Duplicate URL | `source_url` already written to `rd_insights` (URL dedup gate) |
| Near-duplicate summary | Cosine ≥0.96 vs prior insights (semantic dedup gate — see dedup-policy.md) |

---

## Summary Quality Standard

The Haiku-generated summary must:
- Be ≤80 words
- Name the specific product, company, or technology
- State the specific implication for Omerion's packages or competitive position
- NOT quote the body text verbatim
- NOT exceed the article's actual stated claims

**Good summary example:**
> "Acme's AI SDR auto-qualifies and books inbound leads for B2B sales orgs. $25M Series B
> targets the same high_velocity ICP as Omerion's revenue_acceleration_engine. Direct
> product overlap at $25M funding level warrants immediate monitoring."
> `impact_tag: daam | estimated_priority: high`

**Bad summary example (reject):**
> "AI is transforming the business landscape with new tools and capabilities that are
> disrupting traditional workflows across industries."
> (Too generic; no specific product; no ICP overlap; should be `low` not `high`)

---

## Competitive Threat Flag Protocol

When a signal describes a direct competitor, TRACK must:
1. Force `estimated_priority = "high"`
2. Set `impact_tag` to the affected service package (e.g., `daam` for an AI SDR)
3. Include explicit competitive framing in the summary (e.g., "targets same ICP as DAAM")
4. The signal flows to R3's next synthesis run with elevated weight via `estimated_priority`

This is not optional — competitive threats are the highest-value signals in the
entire intelligence pipeline.
