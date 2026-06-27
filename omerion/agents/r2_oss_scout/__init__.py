"""R2 Open-Source Scout & Reverse-Engineer."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("r2-oss-scout", runtime="langgraph", handler=_build())
