# R2 Integration Rubric

Last updated: 2026-06-03
Maintained by: R2 OSS Scout (Agent #12)

## Rubric Weights (Overall Score Formula)

```
overall = fit × 0.40 + maturity × 0.25 + composability × 0.25 + (1 - risk) × 0.10
```

All dimensions are floats in [0.0, 1.0]. Overall scores below 0.50 are not persisted.

---

## Dimension Definitions

### fit (weight 0.40)
How well the repo solves a problem Omerion agents actually face.

| Score | Meaning |
|-------|---------|
| 0.90–1.00 | Directly implements a function Omerion needs today (e.g. structured output parser, HITL queue) |
| 0.70–0.89 | Closely related; would replace 1–2 handwritten modules with minimal wrapping |
| 0.50–0.69 | Peripheral fit; useful only in narrow edge cases or specific agent contexts |
| 0.30–0.49 | Adjacent technology; interesting but not actionable without major adaptation |
| 0.00–0.29 | No fit; keep as reference_only if maturity is very high |

### maturity (weight 0.25)
Maintenance velocity and community health signals.

| Score | Signals |
|-------|---------|
| 0.90–1.00 | Active releases in past 3 months, >500 stars, >2 maintainers, comprehensive docs |
| 0.70–0.89 | Release in past 6 months, clear changelog, responsive issues |
| 0.50–0.69 | Last release 6–18 months ago; issues open but unresponded |
| 0.30–0.49 | Stale (18–36 months); project may be in maintenance mode |
| 0.00–0.29 | Abandoned or single-commit; treat as reference_only maximum |

**Hard rule:** maturity > 0.30 is prohibited for repos with last commit > 18 months ago.

### composability (weight 0.25)
How cleanly the repo integrates with Omerion's LangGraph + Pydantic + asyncio stack.

| Score | Meaning |
|-------|---------|
| 0.90–1.00 | Pure Python library, typed, async-native, no global state, zero config to wire |
| 0.70–0.89 | Sync but wrappable; minor adapter required |
| 0.50–0.69 | Requires subprocess or HTTP sidecar; possible as MCP tool |
| 0.30–0.49 | Framework-specific (Django/Flask) or requires significant monkey-patching |
| 0.00–0.29 | CLI-only or fundamentally incompatible runtime model |

### risk (weight 0.10, inverted)
License + supply-chain exposure. High risk lowers overall score.

| Score | Triggers |
|-------|---------|
| 0.80–1.00 | Copyleft (GPL-2/3, AGPL, SSPL) detected — forced floor regardless of analysis |
| 0.50–0.79 | Single maintainer with no org backing; no releases in 12+ months |
| 0.20–0.49 | Some supply-chain unknowns; CC or non-standard license |
| 0.00–0.19 | MIT/Apache/BSD with active org backing; clean dep tree |

---

## Integration Type Definitions

| Type | When to assign | Implications |
|------|---------------|-------------|
| `component` | Library is directly imported and called within an agent's `tools.py` or `graph.py` | Full API compatibility required; must pass composability >= 0.65 |
| `pattern` | Design pattern is extracted and re-implemented in Omerion code (no direct dependency) | Low supply-chain risk; license irrelevant for production |
| `full_module` | Entire repo is vendored or forked as a first-class Omerion module | Requires founder approval; license must be MIT/Apache/BSD |
| `reference_only` | Repo is read for inspiration only; no code or API enters production | Always safe regardless of license or maturity |

**Hard rules:**
- GPL-2.0, GPL-3.0, AGPL-3.0, SSPL-1.0 → `integration_type` must be `reference_only` if fit >= 0.70
- `full_module` requires composability >= 0.75 AND maturity >= 0.70
- `component` requires maturity >= 0.55

---

## Escalation to Sonnet

Haiku scores `risk > 0.5` → auto-escalate to Sonnet for re-evaluation.
Sonnet's score is final and overwrites Haiku's. `scored_by` field records which model issued the final score.
