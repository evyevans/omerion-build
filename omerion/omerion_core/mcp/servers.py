"""Declarative MCP server config for Claude Agent SDK runtime.

Each entry is passed directly to `ClaudeAgentOptions(mcp_servers=...)`.
Servers are stdio-launched on demand by the SDK; nothing here connects
at import time.

Server selection notes:
  * Google Workspace — local FastMCP Python server at
    omerion_core/mcp/google_workspace_server.py. Exposes 23 tools across
    Gmail (crm_nurture, biz_dev_outreach), Drive (client_onboarding),
    Docs (build_orchestrator), and Sheets (CRM sync). Auth via google_auth.py
    refresh-token flow; all four services share one OAuth grant.
  * GitHub — modelcontextprotocol/servers reference implementation; PAT auth.
  * Supabase — official supabase-community MCP; service-role key.
  * Firecrawl — used by market_mapper, r1_market_tech_watcher, r2_oss_scout
    for structured web scraping.

Pinecone is deliberately NOT exposed over MCP today — direct `pinecone_client`
is faster for the vector-write hot path (embedding-then-upsert).
"""
from __future__ import annotations

from typing import Any

from omerion_core.settings import settings


def server_config(name: str) -> dict[str, Any] | None:
    """Return a single named server entry, or None if creds are missing.

    Useful for agents that only need one connector — they avoid spawning
    unrelated servers (e.g. the lead scraper needs Firecrawl, not GitHub).
    """
    cfg = MCP_SERVERS.get(name)
    if cfg is None:
        return None
    return {name: cfg}


def _google_workspace() -> dict[str, Any] | None:
    if not settings.google_oauth_refresh_token:
        return None
    from pathlib import Path
    server_path = str(Path(__file__).parent / "google_workspace_server.py")
    return {
        "command": "uv",
        "args": [
            "--directory", str(Path(__file__).parent.parent.parent),
            "run", "python", server_path,
        ],
        "env": {
            "GOOGLE_OAUTH_CLIENT_ID": settings.google_oauth_client_id,
            "GOOGLE_OAUTH_CLIENT_SECRET": settings.google_oauth_client_secret,
            "GOOGLE_OAUTH_REFRESH_TOKEN": settings.google_oauth_refresh_token,
        },
    }


def _github() -> dict[str, Any] | None:
    if not settings.github_token:
        return None
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": settings.github_token},
    }


def _supabase() -> dict[str, Any] | None:
    if not (settings.supabase_url and settings.supabase_service_role_key):
        return None
    return {
        "command": "npx",
        "args": ["-y", "@supabase/mcp-server-supabase"],
        "env": {
            "SUPABASE_URL": settings.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
        },
    }


def _firecrawl() -> dict[str, Any] | None:
    # Firecrawl key lives outside the main .env; agents that need it read
    # FIRECRAWL_API_KEY directly from the environment at spawn time.
    import os
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return None
    return {
        "command": "npx",
        "args": ["-y", "firecrawl-mcp"],
        "env": {"FIRECRAWL_API_KEY": key},
    }


def _linkedin() -> dict[str, Any] | None:
    """LinkedIn MCP server — profile scraping + outreach queue management.

    Serves both linkedin_outreach (queue DMs/connection requests, drain queue)
    and lead_scraper_enricher (get_profile, search_company_employees, find_profile_url).

    Requires at minimum: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY for queue tools.
    Scraping tools additionally need PROXYCURL_API_KEY or FIRECRAWL_API_KEY.
    """
    import os
    from pathlib import Path

    server_path = str(Path(__file__).parent.parent.parent.parent / "mcp-servers" / "linkedin_mcp" / "server.py")

    env: dict[str, str] = {}
    for var in (
        "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
        "PROXYCURL_API_KEY", "FIRECRAWL_API_KEY",
        "LINKEDIN_SESSION_COOKIE",
        "OMERION_API_URL", "OMERION_WEBHOOK_TOKEN",
    ):
        val = os.environ.get(var) or getattr(settings, var.lower(), "")
        if val:
            env[var] = val

    # Server requires at least Supabase credentials for queue operations.
    if not (env.get("SUPABASE_URL") and env.get("SUPABASE_SERVICE_ROLE_KEY")):
        return None

    return {
        "command": "uv",
        "args": ["run", "--with", "mcp[cli],httpx,pydantic,supabase", "python", server_path],
        "env": env,
    }


def _build_servers() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, builder in (
        ("google_workspace", _google_workspace),
        ("github", _github),
        ("supabase", _supabase),
        ("firecrawl", _firecrawl),
        ("linkedin", _linkedin),
    ):
        cfg = builder()
        if cfg is not None:
            out[name] = cfg
    return out


# Populated at import time from current settings. Tests that monkeypatch
# settings should call `_build_servers()` again.
MCP_SERVERS: dict[str, dict[str, Any]] = _build_servers()
