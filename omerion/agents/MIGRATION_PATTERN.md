# Wrapper Migration Pattern (Wave 1.9)

Every agent in `omerion/agents/` needs to register a contract with
`agent_wrapper`. This file is the canonical recipe. `linkedin_outreach/`
is the working exemplar — copy its shape.

## Why

Until an agent has a contract, the wrapper runs it with permissive
defaults: input passes through untyped, output is not strictly validated,
style-guard runs over any field but the wrapper doesn't know which
recipients to verify. A migrated agent gets:

- **Cohort opt-out filtering** before the LLM sees contacts
- **Output schema validation** (Pydantic on the agent's final state)
- **Style-guard hard filter** over `human_facing_drafts`
- **Recipient verification** — every contact_id in output must be in input cohort
- **Confidence-threshold HITL routing** below the contract's floor
- **Value-bound enforcement** for any agent that produces dollar amounts

## The recipe (3 files)

### 1. `agents/<name>/contracts.py`

```python
from typing import Literal
from omerion_core.runtime.agent_wrapper import (
    AgentContract, AgentInput, AgentOutput, register_contract,
)

class <Name>Input(AgentInput):
    skill: Literal["<kebab-case-skill>"] = "<kebab-case-skill>"
    # ... per-agent required fields

class <Name>Output(AgentOutput):
    # ... fields the agent emits (e.g., sent_count, drafts, etc.)

CONTRACT = AgentContract(
    skill="<kebab-case-skill>",
    input_model=<Name>Input,
    output_model=<Name>Output,
    min_confidence=0.65,           # below this → HITL
    value_extractor=None,          # only for agents that write dollars
    requires_human_approval_above_value_usd=None,
    mutex_ttl_seconds=1800,
)

register_contract(CONTRACT)
```

### 2. `agents/<name>/__init__.py`

Add the contracts import *before* the registry register call:

```python
from . import contracts  # noqa: F401  — side-effect: register_contract
from .graph import build as _build

register("<skill>", runtime="langgraph", handler=_build())
```

### 3. (Optional) `agents/<name>/tools.py` cleanup

If the agent reaches into `omerion_core.outreach.style_guard` to embed
`STYLE_GUARD_RULES` into its prompt, you can remove that embedding —
the wrapper enforces style as a post-AI hard filter, so the soft prompt
guidance is no longer load-bearing. The agent's prompts get shorter,
the LLM has less negative context to consume, and the guarantee is
stronger.

## Per-agent settings cheat sheet

| Agent                       | min_confidence | Has dollar values? | mutex_ttl |
|-----------------------------|----------------|--------------------|-----------|
| linkedin-outreach           | 0.65           | no                 | 1800      |
| crm-nurture                 | 0.65           | no                 | 1800      |
| icp-scoring                 | 0.70           | no                 | 600       |
| lead-scraper                | 0.60           | no                 | 1800      |
| market-mapper               | 0.60           | no                 | 1800      |
| hq-lead-scraping            | 0.70           | no                 | 1800      |
| offer-matching              | 0.70           | **yes** (Wave 2.2) | 1800      |
| meeting-intelligence        | 0.70           | no                 | 3600      |
| build-orchestrator          | 0.75           | no                 | 3600      |
| outcome-attribution         | 0.80           | **deterministic**  | 1800      |
| client-onboarding           | 0.70           | no                 | 1800      |
| biz-dev-outreach            | 0.65           | no                 | 1800      |
| r1-market-tech-watcher      | 0.55           | no                 | 1800      |
| r2-oss-scout                | 0.60           | no                 | 1800      |
| r3-strategic-architect      | 0.65           | no                 | 1800      |

**offer-matching** is the only agent that owns a `value_extractor`. Its
contract carries `requires_human_approval_above_value_usd=MAX_OPPORTUNITY_VALUE_USD`
and an extractor that pulls the bucket-mapped dollar amount from the
output. See Wave 2.2 for the bucket scheme.

**outcome-attribution** does not get a `value_extractor` because it
should not produce a dollar value at all — its `value_usd` writes are
blocked at the `business_outcomes.record_outcome()` source-of-truth
gate (Wave 2.3).

## Order of migration

Suggested by stakes (highest first):

1. `linkedin-outreach`         ✅ done (exemplar)
2. `offer-matching`             (value bounds — pairs with Wave 2.2)
3. `crm-nurture`                (outreach — recipient guarantees)
4. `outcome-attribution`        (revenue — pairs with Wave 2.3)
5. `meeting-intelligence`       (blueprints — pairs with Wave 2.4)
6. `build-orchestrator`         (deployments)
7. `icp-scoring`                (scoring fan-out)
8. `lead-scraper`               (account creation — pairs with Wave 2.6)
9. `hq-lead-scraping`           (research)
10. `market-mapper`             (cron)
11. `biz-dev-outreach`          (job applications)
12. `r3-strategic-architect`    (synthesis)
13. `r1-market-tech-watcher`    (RSS tagging)
14. `r2-oss-scout`              (repo scoring)
15. `client-onboarding`         (intake)

Each migration is a single-PR change: add `contracts.py`, update
`__init__.py`. The agent's graph runs unchanged. Test by sending a Discord
message that triggers the skill and verifying the wrapper logs:

```
wrapper_idempotency_dedup     # second send within 60s is a no-op
wrapper_cohort_optout_filtered # opted-out contacts are dropped
wrapper_post_validation_style  # style-guard violation routes to HITL
wrapper_recipient_not_in_cohort # hallucinated recipient is rejected
```
