"""Build Orchestrator (Agent #9)."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("build-orchestrator", runtime="langgraph", handler=_build())
