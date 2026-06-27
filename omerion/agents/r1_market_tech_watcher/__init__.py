"""R1 Market/Tech Watcher — daily ingest of RSS, GitHub releases, newsletters."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("r1-market-tech-watcher", runtime="langgraph", handler=_build())
