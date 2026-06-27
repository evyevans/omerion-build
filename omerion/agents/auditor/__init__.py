"""AUDITOR — Constitutional Guardian of the Omerion Agency (RSI Agent #5)."""
from omerion_core.runtime.registry import register

from .graph import build as _build

register("auditor", runtime="langgraph", handler=_build())
