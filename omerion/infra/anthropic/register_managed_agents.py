"""Register (or re-register) all four R-agents with Anthropic Managed Agents.

Usage:
    python -m infra.anthropic.register_managed_agents              # register all
    python -m infra.anthropic.register_managed_agents r1 r3        # subset
    python -m infra.anthropic.register_managed_agents --list       # show remote state
    python -m infra.anthropic.register_managed_agents --trigger r4 # manual run

After a successful registration the remote agent id is printed. Store
it in `.env` as `ANTHROPIC_MANAGED_AGENT_R{N}_ID` if you want the
control-plane webhook handler to resolve incoming webhooks to an agent.

Re-running is safe — the Managed Agents API upserts on `name`.
"""
from __future__ import annotations

import argparse
import json
import sys

from agents.r1_market_tech_watcher.managed_agent import spec as r1_spec
from agents.r2_oss_scout.managed_agent import spec as r2_spec
from agents.r3_strategic_architect.managed_agent import spec as r3_spec
from agents.r4_evaluation_telemetry.managed_agent import spec as r4_spec
from omerion_core.runtime.managed_agents import (
    list_agents,
    register_spec,
    trigger_session,
)

SPECS = {
    "r1": r1_spec,
    "r2": r2_spec,
    "r3": r3_spec,
    "r4": r4_spec,
}


def _register(keys: list[str]) -> int:
    for k in keys:
        if k not in SPECS:
            print(f"unknown agent: {k}", file=sys.stderr)
            return 2
        spec = SPECS[k]()
        try:
            body = register_spec(spec)
        except Exception as exc:  # noqa: BLE001 — surface, don't wrap
            print(f"[{k}] FAILED: {exc}", file=sys.stderr)
            return 1
        print(f"[{k}] registered → id={body.get('id')}  name={spec.name}")
    return 0


def _list() -> int:
    agents = list_agents()
    print(json.dumps(agents, indent=2))
    return 0


def _trigger(key: str, agent_id: str | None) -> int:
    if key not in SPECS:
        print(f"unknown agent: {key}", file=sys.stderr)
        return 2
    spec = SPECS[key]()
    if not agent_id:
        for a in list_agents():
            if a.get("name") == spec.name:
                agent_id = a.get("id")
                break
    if not agent_id:
        print(f"no remote agent_id for {spec.name} — register first", file=sys.stderr)
        return 1
    body = trigger_session(agent_id, mode="background")
    print(json.dumps(body, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("keys", nargs="*", default=list(SPECS.keys()))
    p.add_argument("--list", action="store_true")
    p.add_argument("--trigger", metavar="KEY")
    p.add_argument("--agent-id", default=None)
    args = p.parse_args()

    if args.list:
        return _list()
    if args.trigger:
        return _trigger(args.trigger, args.agent_id)
    return _register(args.keys)


if __name__ == "__main__":
    raise SystemExit(main())
