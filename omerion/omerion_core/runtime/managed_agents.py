"""Claude Managed Agents — registration + trigger helpers.

R1–R4 run in Anthropic's cloud-managed agent runtime (beta header
`managed-agents-2026-04-01`), not inside our local FastAPI process.
Each R-agent exposes a `spec()` function that returns a ManagedAgentSpec;
this module posts those specs to Anthropic's management API and stores
the returned agent id in Supabase (`managed_agent_registrations`).

The exact request shape is isolated in `_register_http()` so library
updates only touch that function — per-agent spec() functions are stable.

Webhook callbacks (agent completed / HITL / error) come back to the
FastAPI control plane via signed POSTs; those endpoints land in
`inbound/routes/control_plane.py` (Phase 8 work) — until then, trigger
runs with `trigger_session(..., mode="foreground")` to get the output
synchronously and persist it ourselves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from omerion_core.logging import get_logger
from omerion_core.settings import settings

log = get_logger("omerion.runtime.managed_agents")

_ANTHROPIC_BASE = "https://api.anthropic.com/v1"


@dataclass
class ManagedAgentSpec:
    """Declarative definition of an R-agent that lives in Anthropic's cloud."""

    name: str                      # e.g. "omerion.r1_market_tech_watcher"
    display_name: str
    model: str                     # e.g. "claude-sonnet-4-6"
    system_prompt: str
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    schedule: str | None = None    # cron expression; None = manual/event-only
    webhook_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.3
    metadata: dict[str, Any] = field(default_factory=dict)


def _beta_headers() -> dict[str, str]:
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required to manage R-agents.")
    return {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": settings.anthropic_managed_agents_beta,
        "content-type": "application/json",
    }


def _spec_payload(spec: ManagedAgentSpec) -> dict[str, Any]:
    """Wire shape sent to Anthropic. Isolated so upstream changes are one-liners."""
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "model": spec.model,
        "system": spec.system_prompt,
        "mcp_servers": spec.mcp_servers,
        "allowed_tools": spec.allowed_tools,
        "schedule": spec.schedule,
        "webhook_url": spec.webhook_url,
        "default_sampling": {
            "max_tokens": spec.max_tokens,
            "temperature": spec.temperature,
        },
        "metadata": spec.metadata,
    }


def register_spec(spec: ManagedAgentSpec) -> dict[str, Any]:
    """Upsert a managed-agent definition; returns the Anthropic response body."""
    resp = httpx.post(
        f"{_ANTHROPIC_BASE}/managed_agents",
        headers=_beta_headers(),
        json=_spec_payload(spec),
        timeout=30.0,
    )
    resp.raise_for_status()
    body = resp.json()
    log.info("managed_agent_registered", name=spec.name, agent_id=body.get("id"))
    return body


def trigger_session(
    managed_agent_id: str,
    inputs: dict[str, Any] | None = None,
    mode: Literal["foreground", "background"] = "background",
) -> dict[str, Any]:
    """Kick a manual run. Foreground blocks and returns the full result."""
    resp = httpx.post(
        f"{_ANTHROPIC_BASE}/managed_agents/{managed_agent_id}/sessions",
        headers=_beta_headers(),
        json={"inputs": inputs or {}, "mode": mode},
        timeout=120.0 if mode == "foreground" else 15.0,
    )
    resp.raise_for_status()
    return resp.json()


def list_agents() -> list[dict[str, Any]]:
    resp = httpx.get(
        f"{_ANTHROPIC_BASE}/managed_agents",
        headers=_beta_headers(),
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])
