"""TRAINER — Chief Intelligence Officer (Agent #16).

Wave 5. Runs weekly. Identifies underperforming prompts across the 6
wrapper-migrated agents, asks an LLM to propose rewrites grounded in
failure telemetry, persists proposals to `prompt_improvements`, and
routes every proposal through founder HITL.

Hard guarantees:
- Never alters input/output schemas (text-only proposals).
- Never auto-applies changes to prompts.py (founder edits manually
  after approval).
- Never analyzes agents that aren't in TRAINER_TARGET_AGENTS.
"""
from omerion_core.runtime.registry import register

# Side-effect: registers AgentContract (min_confidence=0.75, no value_extractor).
from . import contracts  # noqa: F401
from .graph import build as _build

register("trainer", runtime="langgraph", handler=_build())
