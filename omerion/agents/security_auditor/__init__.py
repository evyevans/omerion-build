from __future__ import annotations
from omerion_core.runtime.registry import register
from .graph import build as _build

register("security-auditor", runtime="langgraph", handler=_build())
__all__ = ["build"]
