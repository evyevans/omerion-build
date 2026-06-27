# TWATR Agent Migration Design
**Date:** 2026-06-01  
**Status:** Approved — awaiting Department 1 go-ahead  
**Scope:** All 21 Omerion agents — full migration from LangGraph/FastAPI to Claude Agent SDK + MCP

---

## 1. What We Are Doing and Why

The existing system is built on LangGraph state machines with rigid, deterministic graph nodes. Every agent has a `graph.py` that hard-codes execution order, a `state.py` Pydantic model that pre-defines every field, and `tools.py` functions that make direct HTTP calls to external APIs with no abstraction layer.

This architecture is too brittle. Changing an agent's behaviour requires rewriting graph transitions. Adding a new tool requires plumbing it through state. Debugging is opaque.

**The replacement:** the TWATR framework. Every agent is rebuilt as a fully autonomous Claude Agent SDK instance that reads its own SOP, uses MCP-connected tools, and queries a RAG triad for memory. The only deterministic code left is the trigger that wakes the agent.

---

## 2. The TWATR Framework

```
T  TRIGGER    → Deterministic only. Discord channel map, APScheduler cron,
                Fireflies webhook, Stripe webhook, GitHub PR webhook.
                Resolves to: which agent to invoke + what context to pass.

W  WORKFLOW   → Obsidian vault SOP (.md file). Read at runtime via filesystem MCP.
                Defines step-by-step operating procedure, decision rules,
                output formats, stop conditions, escalation criteria.

A  AGENT      → Claude Agent SDK instance (Python).
                system_prompt = SOP content + constitutional guardrails.
                Fully autonomous: decides its own execution path.
                No graph nodes. No state machine.

T  TOOLS      → MCP servers. Every external capability is a tool.
                Agent calls tools by name; MCP server handles auth + retry.
                15 servers total (see Section 4).

R  RAG        → Three-layer memory / second brain:
                  Pinecone   — semantic vector memory (signals, dossiers, patterns)
                  Supabase   — relational state (CRM truth, run history, state locks)
                  Obsidian   — SOP/knowledge source of truth (git-synced to runtime)
```

---

## 3. What Gets Deleted vs. What Survives

### Deleted from every agent directory
| File | Reason |
|---|---|
| `graph.py` | Replaced by agent.py (Agent SDK) |
| `state.py` | No more Pydantic state models — agent carries context |
| `*_2.py` duplicate files | Artefacts from prior worktree conflicts — clean up |

### Deleted from core infrastructure
| Component | Replacement |
|---|---|
| LangGraph `PostgresSaver` checkpointing | Agent SDK built-in conversation state |
| FastAPI agent-dispatch routing | Trigger → Agent SDK `query()` call |
| `ClaudeRouter` / `Tier` abstraction | Agent SDK model selection |
| `omerion_core/llm/router.py` | Agent SDK handles model routing |

### Survives and migrates
| Component | Fate |
|---|---|
| `tools.py` functions | Migrated into MCP server tool definitions |
| `prompts.py` content | Becomes the Obsidian SOP `.md` file |
| Discord routing / trigger logic | Moves to `trigger.py` per agent |
| APScheduler cron jobs | Moves to `trigger.py` per agent |
| Supabase schema (all tables) | Unchanged |
| Pinecone indexes | Unchanged |
| `omerion_core/` utilities (http, logging, settings, rate_limit) | Survives — used by MCP servers |
| `discord/omerion_bot.py` | Survives — becomes the universal trigger dispatcher |
| Dashboard (`dashboard/`) | Unchanged — deployed to Vercel |

---

## 4. MCP Server Inventory (15 Total)

### 4a. Keep as-is (7)
| Server | What it provides |
|---|---|
| `supabase` | Full Supabase read/write — used by all 21 agents |
| `pinecone` | Vector upsert/query — 8 agents |
| `github` | Repo, PR, issue, file ops — 4 agents |
| `discord` | Send messages, read history, manage channels — 3 agents + notifications |
| `filesystem` | File read/write — healer config patches + **Obsidian SOP reading** |
| `langfuse` | LLM observability traces |
| `token-savior` | Context window management |

### 4b. Upgrade (1)
| Server | Change |
|---|---|
| `google-sheets` → `google-workspace` | Extend to cover Gmail (send/draft), Google Drive (folder/file ops), Google Docs (create/update). Sheets capability retained. |

### 4c. Build from scratch (7) — handled in parallel sub-terminals
| # | Server | External service | Agents |
|---|---|---|---|
| 9 | `firecrawl` | Firecrawl API (`FIRECRAWL_API_KEY`) | hq_lead_scraping, lead_scraper_enricher, biz_dev_outreach |
| 10 | `search` | SerpAPI (`SERP_API_KEY`) + feedparser RSS | market_mapper, biz_dev_outreach, r1_market_tech_watcher |
| 11 | `hunter` | Hunter.io (`HUNTER_API_KEY`) | lead_scraper_enricher |
| 12 | `railway` | Railway GraphQL API (`RAILWAY_API_TOKEN`) | deployer |
| 13 | `linkedin` | Custom HTTP wrapper (existing undocumented endpoint) | linkedin_outreach, lead_scraper_enricher |
| 14 | `fireflies` | Fireflies GraphQL API (`FIREFLIES_API_KEY`) | meeting_intelligence |
| 15 | `google-workspace` | Google OAuth (`GOOGLE_OAUTH_*`) | crm_nurture, biz_dev_outreach, client_onboarding, build_orchestrator |

> **Note:** Servers 9–15 are being built concurrently in parallel sub-terminals.
> This thread handles agent logic and orchestration spec only.

---

## 5. Per-Agent File Structure (New)

Every agent directory is rebuilt to this shape:

```
omerion/agents/<agent_name>/
  __init__.py       ← unchanged
  trigger.py        ← NEW: deterministic entrypoint only
  agent.py          ← NEW: Claude Agent SDK instance + tool wiring
  contracts.py      ← keep if exists (inbound/outbound payload schemas)
  tests/            ← keep, update assertions for new SDK interface
```

Files removed from every agent: `graph.py`, `state.py`, `graph 2.py`, `state 2.py`, `prompts.py` (content moves to Obsidian), `tools.py` (content moves to MCP servers).

---

## 6. Agent SDK Pattern (Canonical Template)

Every `agent.py` follows this exact pattern:

```python
# omerion/agents/<name>/agent.py
from __future__ import annotations

from pathlib import Path

import anthropic
from omerion_core.settings import settings
from omerion_core.logging import get_logger

log = get_logger(__name__)

_SOP_PATH = Path(__file__).parent.parent.parent / "obsidian" / "sops" / "<name>.md"

def _read_sop() -> str:
    if _SOP_PATH.exists():
        return _SOP_PATH.read_text()
    return ""

def _build_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)

def run(context: dict) -> str:
    """
    Invoke the agent with a context dict from trigger.py.
    Returns the agent's final text response.
    """
    client = _build_client()
    sop = _read_sop()

    mcp_servers = _mcp_servers_for_agent()   # returns list of MCPServerHTTP/Stdio configs

    with client.beta.messages.stream(
        model=settings.claude_model_sonnet,
        max_tokens=8096,
        system=sop,
        messages=[{"role": "user", "content": _format_context(context)}],
        betas=["mcp-client-2025-04-04"],
        mcp_servers=mcp_servers,
    ) as stream:
        response = stream.get_final_message()

    return response.content[-1].text
```

> The exact SDK API (`client.beta.messages` vs `query()`) will be confirmed
> against `claude-agent-sdk-python/` source before Department 1 implementation.

---

## 7. Trigger Pattern (Canonical Template)

Every `trigger.py` is deterministic — no Claude calls, no LLM logic:

```python
# omerion/agents/<name>/trigger.py
from __future__ import annotations

from .agent import run

# Discord trigger (called by omerion_bot.py dispatcher)
def from_discord(message: str, author: str, channel: str) -> str:
    return run({"source": "discord", "message": message,
                "author": author, "channel": channel})

# Cron trigger (called by APScheduler in main.py)
def from_cron() -> str:
    return run({"source": "cron"})

# Webhook trigger (called by FastAPI inbound route)
def from_webhook(payload: dict) -> str:
    return run({"source": "webhook", "payload": payload})
```

---

## 8. RAG Triad Usage Pattern

Agents query all three layers via their MCP tools:

```
Pinecone (semantic)
  → Tool: pinecone/search-records
  → When: "find similar past signals", "retrieve outreach patterns",
           "semantic match on pain signals"

Supabase (relational state)
  → Tool: supabase/query or supabase/rpc
  → When: "load contact CRM state", "check last touch date",
           "write run result", "acquire advisory lock"

Obsidian (SOP / knowledge)
  → Tool: filesystem/read
  → Path: obsidian/sops/<agent_name>.md
  → When: agent initialises — reads its own SOP as system prompt context
  → Also: obsidian/knowledge/*.md for shared reference material
```

---

## 9. Obsidian Vault Structure (Runtime)

```
omerion/obsidian/
  sops/
    market_mapper.md
    high_quality_lead_scraping.md
    lead_scraper_enricher.md
    linkedin_outreach.md
    crm_nurture.md
    icp_scoring.md
    offer_matching.md
    biz_dev_outreach.md
    meeting_intelligence.md
    client_onboarding.md
    outcome_attribution.md
    build_orchestrator.md
    builder.md
    validator.md
    r1_market_tech_watcher.md
    r2_oss_scout.md
    r3_strategic_architect.md
    auditor.md
    healer.md
    deployer.md
    trainer.md
  knowledge/
    personas.md           ← 9 ICP personas from agents.yaml
    offer_packages.md     ← 4 service packages + demo catalog
    constitutional_rules.md  ← what agents must never do
    hitl_protocol.md      ← when and how to escalate to founder
```

---

## 10. Deployment Architecture

```
┌─────────────────────────────────────┐  ┌─────────────────────────────────────┐
│           RAILWAY                   │  │              VERCEL                 │
│                                     │  │                                     │
│  omerion/main.py (FastAPI)          │  │  dashboard/  (React + Vite)        │
│    ├── APScheduler (cron triggers)  │  │  omerion/omerion_core/inbound/      │
│    ├── Inbound webhooks             │  │    stripe.py  (webhook receiver)   │
│    └── Health endpoint              │  │    fireflies.py                     │
│                                     │  │                                     │
│  discord/omerion_bot.py             │  │  Vercel Cron → trigger endpoints   │
│    └── Routes Discord → trigger.py │  │                                     │
│                                     │  └─────────────────────────────────────┘
│  Agent runtime (all 21 agents)      │
│    └── Claude Agent SDK             │  ┌─────────────────────────────────────┐
│                                     │  │          MCP SERVERS                │
│  MCP servers (local/Docker)         │  │  (co-located with Railway OR        │
│    └── 15 servers accessible        │  │   separate Railway services)        │
│        via HTTP/stdio               │  └─────────────────────────────────────┘
└─────────────────────────────────────┘
```

---

## 11. Department Execution Order

Each agent is completed fully (trigger → SOP → agent.py → tests) before the next begins.

### Department 1 — Revenue / GTM (8 agents)
1. `market_mapper` — search-mcp, supabase-mcp
2. `high_quality_lead_scraping` — firecrawl-mcp, pinecone-mcp, supabase-mcp
3. `lead_scraper_enricher` — firecrawl-mcp, hunter-mcp, linkedin-mcp, supabase-mcp
4. `linkedin_outreach` — linkedin-mcp, supabase-mcp
5. `crm_nurture` — google-workspace-mcp (Gmail), pinecone-mcp, supabase-mcp
6. `icp_scoring` — pinecone-mcp, supabase-mcp
7. `offer_matching` — pinecone-mcp, supabase-mcp
8. `biz_dev_outreach` — search-mcp, firecrawl-mcp, google-workspace-mcp (Gmail), supabase-mcp

### Department 2 — Client Delivery (3 agents)
9. `meeting_intelligence` — fireflies-mcp, pinecone-mcp, supabase-mcp
10. `client_onboarding` — google-workspace-mcp (Drive/Docs), supabase-mcp
11. `outcome_attribution` — supabase-mcp

### Department 3 — Build / Engineering (3 agents)
12. `build_orchestrator` — github-mcp, google-workspace-mcp (Docs/Drive), supabase-mcp
13. `builder` — github-mcp, supabase-mcp
14. `validator` — github-mcp, supabase-mcp

### Department 4 — Research / Intelligence (3 agents)
15. `r1_market_tech_watcher` — search-mcp (RSS), pinecone-mcp, supabase-mcp
16. `r2_oss_scout` — github-mcp, supabase-mcp
17. `r3_strategic_architect` — supabase-mcp

### Department 5 — RSI (4 agents)
18. `auditor` — supabase-mcp, discord-mcp, filesystem-mcp
19. `healer` — supabase-mcp, pinecone-mcp, filesystem-mcp
20. `deployer` — railway-mcp, supabase-mcp, discord-mcp
21. `trainer` — supabase-mcp

---

## 12. Constitutional Rules (All Agents)

These rules are injected into every agent's system prompt via `obsidian/knowledge/constitutional_rules.md`:

- Never call an API not listed in your approved MCP tool set
- Always escalate to `#founder-hitl` before taking irreversible actions (send email, merge PR, deploy, charge money)
- Never modify `docker/`, `.github/workflows/`, or `railway.toml`
- Never expose secrets, tokens, or API keys in any output
- Never retry a HITL-rejected action without a new approval
- Never spend more than `per_run_cost_cap_usd` in a single run (checked by trigger.py)
- Log every tool call result to Supabase `agent_telemetry` via supabase-mcp

---

## 13. HITL Protocol

When an agent must escalate:
1. Post a structured card to `#founder-hitl` via discord-mcp
2. Write a `founder_review_queue` row to Supabase with `status = 'pending'`
3. Halt — do not proceed until `status` becomes `'approved'` or `'rejected'`
4. On `approved`: resume with the approved context
5. On `rejected`: write `status = 'hitl_rejected'` to `agent_runs`, notify originating channel, stop

---

## 14. Pre-Implementation Checklist (Per Agent)

Before writing `agent.py` for any agent, verify:
- [ ] SOP `.md` written to `obsidian/sops/<name>.md`
- [ ] All required MCP servers confirmed available (built by sub-terminals)
- [ ] Supabase tables the agent reads/writes confirmed to exist (ref: MASTER_MIGRATION.sql)
- [ ] `trigger.py` entrypoints identified (Discord channel, cron schedule, or webhook)
- [ ] Constitutional rules injected (via `obsidian/knowledge/constitutional_rules.md`)
- [ ] At least one test in `tests/` that invokes `trigger.py` and asserts a DB write

---

## 15. Open Questions (To Resolve Per Agent)

These will be answered as we reach each agent:

| Agent | Open question |
|---|---|
| `linkedin_outreach` | Which LinkedIn endpoint does the custom HTTP wrapper hit? Need URL + auth method confirmed. |
| `biz_dev_outreach` | Upwork RSS feeds require no auth — but Wellfound/YC scraping via Firecrawl needs confirmation of which URLs are scrapeable vs. blocked. |
| `builder` | Test execution (subprocess `uv run pytest`) in agent context — confirm sandbox approach without LangGraph. |
| `deployer` | Railway service ID and project ID — confirm env vars are set before agent can run. |
| `meeting_intelligence` | Fireflies webhook secret — confirm `FIREFLIES_WEBHOOK_SECRET` is set and webhook is registered. |
