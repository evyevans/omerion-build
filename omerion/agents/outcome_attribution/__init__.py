"""Outcome Attribution & Feedback (Agent #10).

Wave 1.9 migration: wrapper contract with the strictest confidence
floor (0.80) and no dollar-value extraction. The agent narrates the
KPI delta; any business_outcomes write goes through the source-of-truth
gate (Wave 2.3) with source='deterministic_compute' — never via this
agent's inference.
"""
from omerion_core.runtime.registry import register

# Side-effect: registers AgentContract with strict confidence floor.
from . import contracts  # noqa: F401
from .graph import build as _build

register("outcome-attribution", runtime="langgraph", handler=_build())
