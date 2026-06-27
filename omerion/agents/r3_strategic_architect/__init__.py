"""R3 Strategic Architect (Agent #13)."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("r3-strategic-architect", runtime="langgraph", handler=_build())
