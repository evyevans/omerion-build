# Omerion Production Tech Stack

Last updated: 2026-06-03
Source of truth: omerion/requirements.txt + omerion/config/agents.yaml

This file is R2's reference for scoring `fit` and `composability`. A repo that
wraps an existing dependency adds zero net fit. A repo that replaces a
hand-rolled module adds high fit.

---

## Runtime Stack

| Layer | Technology | Version constraint | Notes |
|-------|------------|-------------------|-------|
| Orchestration | LangGraph (Python) | ^0.2 | StateGraph + checkpointer pattern |
| LLM | Anthropic Claude API | claude-sonnet-4-6 / haiku-4-5 | Via `omerion_core.llm.router.ClaudeRouter` |
| State persistence | Supabase (PostgreSQL 15) | hosted | All agent state, HITL, CRM |
| Vector DB | Pinecone | serverless | One index per dept, namespace-isolated |
| Deployment | Railway | — | Unified container + stdio MCP sub-processes |
| HTTP client | httpx | ^0.27 | async-capable, used in tools |
| Data validation | Pydantic v2 | ^2.7 | All state models, strict mode |
| Task queue | APScheduler (in-process) | ^3.10 | No separate worker service |
| Web scraping | Firecrawl API | — | via MCP tool |
| Email enrichment | Hunter.io API | — | REST |
| Search | SerpAPI | — | Google organic results |
| Logging | structlog | — | via `omerion_core.logging.get_logger` |
| Tracing | Langfuse | — | traced_node decorator on all graph nodes |
| Discord | discord.py | ^2.3 | Bot for HITL cards + channel routing |
| Auth | Railway env vars | — | No OAuth in agent runtime |

## Agent Runtime Pattern

Every production agent follows this structure:
- `graph.py` — StateGraph compiled with `get_checkpointer()` for HITL agents
- `tools.py` — pure functions called from nodes; no LangGraph imports
- `state.py` — Pydantic models extending `AgentRunState`
- `contracts.py` — `AgentContract` registered at import
- `prompts.py` — string constants only; imports `UNIVERSAL_AGENT_RULES`

## Scoring Guidance for R2

### High fit targets (what Omerion would benefit from)
- Structured JSON extraction from LLM responses (currently hand-rolled `extract_json_object`)
- Async rate-limiter / token bucket for API calls (currently ad-hoc retry in `safe_request`)
- Semantic chunking for Pinecone upserts (currently fixed-length text splits)
- HITL workflow primitives (currently custom `gate()` implementation)
- Supabase row-level pagination helpers (currently manual `.limit()` chaining)

### Zero fit (already covered, do not score as "fit")
- LangGraph itself — already in use
- Anthropic SDK — already in use via ClaudeRouter
- Pydantic — already in use
- httpx — already in use
- Pinecone SDK — already in use
- APScheduler — already in use
- discord.py — already in use

### Composability blockers
- Any repo requiring Django/Flask/FastAPI as a dependency (we use uvicorn + raw ASGI)
- Any repo requiring a Redis sidecar (Railway setup doesn't include Redis)
- Any repo with a mandatory CLI entrypoint and no library API
- Any repo importing `asyncio.run()` at module level (breaks our event loop)
