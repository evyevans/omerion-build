# Signal Synthesis Guide — SHAPE (R3 Strategic Architect)

**Maintained by:** SHAPE (r3_strategic_architect, Agent #13)  
**Last updated:** 2026-06-03  
**Purpose:** Governs how SHAPE fuses R1, R2, and PROVE signals when computing Confidence
in the RICE formula. This is the authoritative decision table for signal weighting.

---

## Confidence Level Decision Table

Confidence (C) in the RICE formula represents the strength of evidence behind a proposal.
It is NOT a percentage — it maps directly to a fixed value.

| Signal combination present | Confidence | Notes |
|---|---|---|
| R1 + R2 + PROVE all corroborate | **1.0** | Highest confidence; three independent signal sources agree |
| R1 + R2 OR R1 + PROVE OR R2 + PROVE | **0.8** | Two sources; strong but not fully triangulated |
| R1 only OR R2 only OR PROVE only | **0.5** | Single signal; plausible but needs more validation |
| No signals — hypothesis only | **0.3** | Must explicitly tag as `confidence: 0.3 (hypothesis)` in the proposal |

### What "corroboration" means
Signals corroborate when they independently point to the same operational pain or
opportunity — not just the same topic. A PROVE report showing 12% speed-to-lead
degradation corroborates an R1 insight about CRM lead routing latency. An R2 OSS
candidate for a webhook library does NOT corroborate that same PROVE report unless
the integration is explicitly named as the solution.

---

## OSS Maturity Bonus (R2 only)

When an R2 candidate in the `supporting_oss_ids` list carries the `[maturity:rising]`
flag (rescore_history shows Δmaturity ≥ 0.05 across two or more scoring runs), apply
a **+0.1 Confidence bonus**, capped at 1.0.

```
If any supporting OSS candidate has [maturity:rising]:
    C = min(C + 0.1, 1.0)
```

**Rationale:** Rising maturity signals active maintenance and adoption velocity —
the integration is less likely to stall mid-build. This is a minor bonus, not a
primary signal driver.

---

## Signal Override Rules

When signals conflict, apply this priority hierarchy:

1. **PROVE attribution reports** — highest authority. If PROVE shows a KPI delta
   that contradicts an R1 insight (e.g., R1 says "CRM latency is a market concern"
   but PROVE shows deployed CRM automation already solved it for Omerion clients),
   PROVE wins. Do not propose a solution to a problem already solved.

2. **R1 market/tech insights** — second authority. Represent external market
   signals. Can initiate a hypothesis but cannot override PROVE data.

3. **R2 OSS candidates** — supporting evidence only. An OSS candidate alone does
   not justify a proposal without an R1 or PROVE signal establishing the underlying
   need. Exception: an OSS candidate with `[maturity:rising]` AND `integration_type
   = "drop-in"` AND `overall_score ≥ 0.85` may anchor an `internal_os` proposal.

---

## Signal Freshness Rules

The lookback window is **14 days** (state.py default). Signals older than 14 days
are NOT loaded by `load_signals()`.

Within the 14-day window, apply a soft staleness penalty in the narrative:
- Signals from days 1–7: use directly, no staleness note required.
- Signals from days 8–14: flag as `[8–14d old]` in the `design_doc_md` section
  to ensure the founder review card surfaces the age.
- Never reject a valid signal purely because it is 8–14 days old — RICE still applies.

---

## Worked Confidence Examples

**Example A: DAAM speed-to-lead proposal**
- R1: 3 insights tagged `daam` about CRM lead latency in the 14-day window
- R2: OSS webhook queue library, `overall_score=0.88`, `[maturity:rising]`
- PROVE: 1 report with `kpi_deltas` showing `speed_to_lead: -18%` (significant)
- All three sources corroborate → C = 1.0 → C + 0.1 OSS bonus → cap at 1.0

**Example B: CAPA exec time recovery proposal**
- R1: 2 insights tagged `capa` about CRM time-sink for ops leaders
- R2: none relevant
- PROVE: none
- Two R1 signals (same topic) = single R1 source, not two corroborating sources
- C = 0.5 → only 1 signal source even though 2 rows

**Example C: ASAP hypothesis proposal**
- No R1, no R2, no PROVE signals in window
- Founder mentioned doc-gen need in last call (not a signal source)
- Must use C = 0.3 and explicitly mark `confidence: 0.3 (hypothesis)` in output

---

## Approved Patterns (write-back — continuous improvement)

> **This section is the continuous-learning loop.** Every founder decision teaches
> SHAPE what synthesis *worked*. The loop has two halves:
>
> 1. **Pinecone (runtime, automatic):** `emit_node` calls
>    `signals.write_proposal_signal()` for every *decided* proposal (approved AND
>    rejected) into the `intelligence_r3` namespace. On the next run,
>    `retrieve_prior_node` recalls the top-3 semantically-nearest priors and seeds
>    the synthesis prompt — APPROVED as precedent to extend, REJECTED as
>    "do-not-repeat." This half is live code; no human action required.
>
> 2. **Obsidian (curated, this section):** when an approved proposal later ships and
>    its 90-day PROVE attribution confirms the KPI moved, distil the winning
>    `problem → hypothesis → target_module` shape into one bullet below. This is the
>    durable, human-readable precedent that survives Pinecone re-indexing and trains
>    future skill.md edits.

**Write-back trigger (Obsidian):** append a bullet here when
`attribution_reports.kpi_deltas` shows a *significant* positive delta for a
deployment whose `blueprint_handoff` traces back to an approved R3 proposal.

**Format (one line per confirmed win):**
`- [YYYY-MM-DD] <target_module> · "<title>" → KPI <metric> <delta> · proposal_id=<uuid>`

**Confirmed wins:**
<!-- SHAPE appends below. Keep newest first. Max 25 bullets — prune the oldest. -->
- _(none yet — first confirmed win lands here once a shipped proposal's PROVE report closes)_

**Read-back rule:** at synthesis, the top 5 most recent confirmed wins in this
section are the *only* Obsidian precedent SHAPE should weight above Pinecone recall,
because an attribution-confirmed win outranks a merely-approved one. Cap the pull at
400 tokens (≈ the 25-bullet ceiling).
