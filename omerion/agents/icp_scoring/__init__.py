"""ICP Fit & Why Now Scoring (Agent #6)."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("icp-scoring", runtime="langgraph", handler=_build())
