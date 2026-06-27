"""High-Quality Lead Scraping (Agent #2) — deep founder-priority dossiers."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("hq-lead-scraping", runtime="langgraph", handler=_build())
