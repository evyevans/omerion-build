"""Offer Matching & Playbook (Agent #7).

Wave 2.1 + 2.2 migration: registers a wrapper contract with
value-bound enforcement (`MAX_OPPORTUNITY_VALUE_USD`) and
deterministic bucket mapping. The LLM never produces dollar amounts.
"""
from omerion_core.runtime.registry import register

# Side-effect: registers AgentContract with value_extractor + value cap.
from . import contracts  # noqa: F401
from .graph import build as _build

register("offer-matching", runtime="langgraph", handler=_build())
