"""Claude-powered intent classifier.

Receives transcribed voice text and routes it to the correct factory
agent or API endpoint. Returns the chosen action's result."""
from __future__ import annotations

import logging
from typing import Any

from core.agents._helpers import extract_json
from core.agents.agent_registry import get_registry
from core.runtime.claude_router import get_router
from core.schemas.base import ModelTier, TenantContext

log = logging.getLogger("omerion.voice.dispatcher")


SYSTEM_PROMPT = """You route a founder's spoken instruction to one factory
action. Choose ONE of:
  - run_agent: {"action":"run_agent","agent":"<name>","payload":{...}}
  - trigger_rsi: {"action":"trigger_rsi","hours":24}
  - status:     {"action":"status"}
  - approve:    {"action":"approve","approval_id":"...","decision":"approve|reject"}
  - unknown:    {"action":"unknown","reason":"..."}

Available agents (use exact names):
  aria, forge, scout, gatekeeper, patcher,
  mapper, enrich, icp_scorer, outreach, nurture, sentinel,
  librarian, strategist, analyst, competitor,
  scribe, proposer, attribution, success_ops,
  observer, ux_reviewer, prompt_optimizer, rag_auditor, token_optimizer,
  seeker, synthesis

Strict JSON. No prose."""


async def dispatch_intent(ctx: TenantContext, text: str) -> dict[str, Any]:
    router = get_router()
    resp = await router.complete(
        tier=ModelTier.FAST,
        system=SYSTEM_PROMPT,
        prompt=f"Founder said: {text!r}\n\nReturn the routing JSON.",
        max_tokens=300, temperature=0.0,
        client_slug=ctx.client_slug, agent="voice.action_dispatcher",
    )
    try:
        action = extract_json(resp.text)
    except ValueError:
        return {"ok": False, "reason": "could not parse intent",
                "raw": resp.text}
    if not isinstance(action, dict):
        return {"ok": False, "reason": "non-object intent", "raw": action}

    kind = action.get("action")
    reg = get_registry()
    if kind == "run_agent":
        agent_name = action.get("agent")
        payload = action.get("payload") or {}
        if not agent_name:
            return {"ok": False, "reason": "no agent specified"}
        try:
            agent = reg.get(agent_name)
        except KeyError:
            return {"ok": False, "reason": f"unknown agent {agent_name}"}
        out = await agent.run(ctx, payload)
        return {"ok": True, "kind": kind, "agent": agent_name,
                "output": out.model_dump(mode="json")}

    if kind == "trigger_rsi":
        from departments.recursive_self_improvement.rsi_workflow import run_rsi_loop
        return {"ok": True, "kind": "trigger_rsi",
                "result": await run_rsi_loop(ctx, hours=int(action.get("hours", 24)))}

    if kind == "status":
        return {"ok": True, "kind": "status",
                "agents_registered": len(reg.list_agents()),
                "departments_enabled": [d.value for d in ctx.departments_enabled]}

    if kind == "approve":
        # Bounce to the approvals route caller-side
        return {"ok": True, "kind": "approve",
                "approval_id": action.get("approval_id"),
                "decision": action.get("decision")}

    return {"ok": False, "kind": "unknown",
            "reason": action.get("reason", "no matching action")}
