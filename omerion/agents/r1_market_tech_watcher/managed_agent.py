# ⚠️  ASPIRATIONAL — NOT THE LIVE RUNTIME PATH ⚠️
# This file describes the future Claude Managed Agent spec for R1.
# The canonical, executed implementation is in graph.py + tools.py + state.py.
# Tables, thresholds, model tiers, and event types referenced below may
# diverge from the live agent — verify against graph.py before relying on
# anything here. Do not invoke from production code.
"""R1 Market/Tech Watcher — Claude Managed Agent spec.

Scans B2B tech + AI-agent feeds daily, tags each signal with the
consulting offer-package it most likely affects, writes insights to
Supabase, and embeds them into Pinecone namespace `rd_insights` for
R3 synthesis.

Runs in Anthropic's cloud on an 06:00 America/Toronto cron.
"""
from __future__ import annotations

from omerion_core.mcp.servers import server_config
from omerion_core.runtime.managed_agents import ManagedAgentSpec
from omerion_core.settings import settings

R1_SYSTEM = """You are Omerion's R&D triage analyst for a general-industry AI automation consulting agency.

For each raw signal (URL + title + body) you fetch via Firecrawl/WebFetch,
output a tight summary and classify it against Omerion's consulting
offer-packages (the `service_package` names we sell).

Output STRICT JSON only:
{
  "summary": "≤80 words, focuses on what changed and why it matters for B2B ops and automation",
  "impact_tag": "daam | capa | remi | asap | internal_os",
  "estimated_priority": "high | medium | low",
  "persona_hits": ["ops_leader", "revenue_leader", ...]   // 0-3 relevant personas from the 9-persona taxonomy
}

Tag routing:
- daam (→ revenue_acceleration_engine): CRM, speed-to-lead, AI outreach, follow-up automation, pipeline tools
- capa (→ ops_intelligence_layer): ops workflow automation, reporting, team performance dashboards
- remi (→ research_decision_stack): market intelligence, research synthesis, strategic data pipelines
- asap (→ process_automation_suite): process automation, doc generation, workflow orchestration, compliance
- internal_os: agent orchestration (LangGraph, MCP, RAG plumbing) — affects Omerion itself

Priority high = immediate adoption candidate OR direct competitive threat (>$10M funding, overlaps our ICP);
medium = watch this quarter; low = informational context.

Write each accepted insight to Supabase table `rd_insights` via the Supabase
MCP server and embed it into Pinecone namespace `rd_insights`.
"""


def spec() -> ManagedAgentSpec:
    mcp_servers: dict = {}
    for name in ("firecrawl", "supabase"):
        cfg = server_config(name)
        if cfg:
            mcp_servers.update(cfg)

    return ManagedAgentSpec(
        name="omerion.r1_market_tech_watcher",
        display_name="R1 · Market & Tech Watcher",
        model="claude-sonnet-4-6",
        system_prompt=R1_SYSTEM,
        mcp_servers=mcp_servers,
        allowed_tools=["mcp:firecrawl.*", "mcp:supabase.*", "web_search"],
        schedule="0 6 * * *",                    # 06:00 daily
        webhook_url=f"{settings.omerion_public_base_url}/webhooks/managed_agents" if settings.omerion_public_base_url else None,
        max_tokens=4096,
        temperature=0.3,
        metadata={"role": "rd_watcher", "tier": "research"},
    )
