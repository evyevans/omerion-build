"""VALIDATOR (Agent #16) — PR QA gatekeeper."""
from omerion_core.runtime.registry import register

from .graph import build as _build

register("validator", runtime="langgraph", handler=_build())
