"""MCP (Model Context Protocol) connectors — the Claude-native data layer.

Each Agent-SDK agent passes `mcp_servers=MCP_SERVERS` into ClaudeAgentOptions;
the SDK handles discovery, tool-listing, and the actual RPC over stdio.

For anything that needs raw Python (telemetry writes, gspread sheet
bootstraps, the CRM mutate endpoint), the thin wrappers in
`omerion_core.clients.*` remain — they share credentials via
`mcp.google_auth.google_credentials()`.
"""
from omerion_core.mcp.servers import MCP_SERVERS, server_config
from omerion_core.mcp.google_auth import google_credentials

__all__ = ["MCP_SERVERS", "server_config", "google_credentials"]
