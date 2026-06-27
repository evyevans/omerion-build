"""Biz Dev Outreach — finds consulting clients via freelance / posting platforms."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("biz-dev-outreach", runtime="langgraph", handler=_build())
