"""HEALER — Autonomous Remediation Engine (RSI Agent #16).

Closes the Health Loop: regression.alert -> diagnose -> patch -> healing.applied -> AUDITOR.
"""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401 — registers AgentContract as side-effect
from .graph import build as _build

register("healer", runtime="langgraph", handler=_build())
