# HITL Flag Conditions

## Flag Labels

**CANONICAL LIST — 12 labels. These are the ONLY valid flag labels for meeting_intelligence.**

Validate LLM output against this list. Discard any label not present here. This list is the single source of truth — it governs `contracts.py KNOWN_FLAG_LABELS`, `prompts.py HITL_FLAG_SYSTEM`, and `agents.yaml hitl_flag_conditions`.

| label | severity | trigger condition |
|---|---|---|
| `unclear_pain` | high | W5H `what` field is `"Unstated — see transcript context."` or contains fewer than 20 words |
| `budget_unconfirmed` | medium | W5H `how_much` is `"Budget not disclosed."` |
| `timeline_aggressive` | medium | `when` mentions a deadline ≤ 30 calendar days from meeting date |
| `scope_ambiguous` | high | Proposal backlog has > 2 tasks with `effort_days = null` OR `acceptance_criteria = []` |
| `stakeholder_missing` | medium | No economic buyer (VP-level or above, C-suite, or business owner) appears in `who` |
| `compliance_concern` | high | Transcript explicitly mentions HIPAA, SOC 2, GDPR, PII handling, "legal review required", or "compliance team" |
| `data_sensitivity` | high | Client mentions proprietary data, NDA requirements, data residency restrictions, or "can't share that externally" |
| `tech_constraint` | low | Client specifies a tech stack, tool lock-in, or integration requirement that conflicts with any canonical package's default tooling |
| `competitor_present` | medium | A competing vendor, agency, or internal team is actively working on the same problem described in `what` |
| `low_meeting_engagement` | low | Transcript contains fewer than 500 words of substantive dialogue OR prospect contributed less than 20% of total word count |
| `scope_exceeds_pricing_band` | high | Proposal backlog total `effort_days` would require >3 months at standard delivery rate for the quoted `price_usd` |
| `persona_tier_mismatch` | medium | Detected `persona_tier` is 1 (entry-level operator) but `recommended_service_package` is priced above $8,000/mo, or tier 3 but priced below $6,000/mo |

## HITL Review Threshold Rules

- Any **high** severity flag → `hitl_requires_review = true`
- **2 or more medium** severity flags → `hitl_requires_review = true`
- Single **medium** only, or **low** only → `hitl_requires_review = false`
- Zero flags → `hitl_requires_review = false`

## Evidence Requirement

Every flag must include an `evidence` string quoting ≤2 sentences verbatim from the transcript. If no direct quote can be found, **do not raise the flag**. Inferred flags without evidence are discarded by `raise_flags()`.

## Output Format

```json
{
  "flags": [
    {
      "label": "unclear_pain",
      "severity": "high",
      "evidence": "Quote from transcript.",
      "confidence": 0.92
    }
  ],
  "requires_review": true
}
```

## Max Flags Per Run

Hard cap: 20 (enforced by `HitlFlags` validator in contracts.py). If LLM returns more than 20, truncate to the 20 with highest confidence scores.