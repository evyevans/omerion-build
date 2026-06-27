# ⚠️  ASPIRATIONAL — NOT THE LIVE RUNTIME PATH ⚠️
# This file describes the future Claude Managed Agent spec for R2.
# The canonical, executed implementation is in graph.py + tools.py + state.py.
# Tables, thresholds, model tiers, and event types referenced below may
# diverge from the live agent — verify against graph.py before relying on
# anything here. Do not invoke from production code.
"""R2 OSS Scout — Claude Managed Agent spec.

Sweeps GitHub / Hacker News / OSS indexes for AI-automation-adjacent projects
(workflow automation, outreach tools, CRM integrations, agent orchestration
frameworks, ops intelligence tools), scores each on
fit/maturity/composability/risk, and files the top N into
Supabase `rd_oss_candidates`.

Runs weekly on Monday 07:00 America/Toronto.
"""
from __future__ import annotations

from omerion_core.mcp.servers import server_config
from omerion_core.runtime.managed_agents import ManagedAgentSpec
from omerion_core.settings import settings

R2_SYSTEM = """You are Omerion's OSS reverse-engineering analyst for a general-industry AI automation consulting agency.

You sweep GitHub for repositories that could accelerate one of Omerion's
consulting offer-packages or the internal agent OS. For each candidate
repo, score it on the rubric and classify it.

Focus queries (rotate across runs):
  - Workflow automation frameworks (Temporal, Prefect, Dagster, LangGraph plugins)
  - CRM outreach and lead enrichment tools (Apollo integrations, Hunter wrappers)
  - AI outreach / SDR automation (email sequencing, reply detection)
  - Ops intelligence and reporting automation
  - Agent orchestration (LangGraph, MCP servers, multi-agent)
  - RAG + vector stores for B2B document corpora

Output STRICT JSON only (one object per candidate):
{
  "fit": 0.0-1.0,                          // alignment to an Omerion service_package or internal_os
  "maturity": 0.0-1.0,                     // stars, commit recency, production readiness
  "composability": 0.0-1.0,                // license permissiveness, modularity
  "risk": 0.0-1.0,                         // 0 = safe, 1 = avoid (GPL viral, unmaintained, security red flags)
  "integration_type": "component | pattern | full_module | reference_only",
  "impact_tag": "daam | capa | remi | asap | internal_os",
  "recommendation": "≤60 words — how Omerion should use this, if at all"
}

Rules:
- Prefer MIT / Apache-2.0 / BSD licenses. Flag GPL/AGPL as high risk for any
  client-facing module.
- "component" = a function/submodule to vendor in.
- "pattern"   = architectural idea to replicate, not code to import.
- "full_module" = drop-in service with minor adaptation.
- "reference_only" = study-only, do not integrate.

Persist accepted candidates to Supabase `rd_oss_candidates` via the Supabase
MCP server.
"""


def spec() -> ManagedAgentSpec:
    mcp_servers: dict = {}
    for name in ("github", "firecrawl", "supabase"):
        cfg = server_config(name)
        if cfg:
            mcp_servers.update(cfg)

    return ManagedAgentSpec(
        name="omerion.r2_oss_scout",
        display_name="R2 · OSS Scout",
        model="claude-sonnet-4-6",
        system_prompt=R2_SYSTEM,
        mcp_servers=mcp_servers,
        allowed_tools=["mcp:github.*", "mcp:firecrawl.*", "mcp:supabase.*", "web_search"],
        schedule="0 7 * * 1",                    # 07:00 Monday
        webhook_url=f"{settings.omerion_public_base_url}/webhooks/managed_agents" if settings.omerion_public_base_url else None,
        max_tokens=4096,
        temperature=0.2,
        metadata={"role": "rd_oss_scout", "tier": "research"},
    )
