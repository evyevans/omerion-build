"""Market Mapper (Agent #1) — weekly market scrape + account ranking."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("market-mapper", runtime="langgraph", handler=_build())
