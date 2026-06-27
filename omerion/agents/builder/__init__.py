from omerion_core.runtime.registry import register

from .graph import build as _build

register("builder", runtime="langgraph", handler=_build())
