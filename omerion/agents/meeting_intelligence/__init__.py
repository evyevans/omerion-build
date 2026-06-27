"""Meeting Intelligence & Consulting Proposal (Agent #8).

Wave 2.4 migration: wrapper contract + typed HitlFlags schema. The
persist node now validates the `hitl_flags` JSONB structure before
write; malformed payloads raise pydantic ValidationError rather than
silently corrupting the founder review UI.
"""
from omerion_core.runtime.registry import register

# Side-effect: registers AgentContract and exposes validate_hitl_flags.
from . import contracts  # noqa: F401
from .graph import build as _build

graph_handler = _build()
register("meeting-intel", runtime="langgraph", handler=graph_handler)
register("meeting-intelligence", runtime="langgraph", handler=graph_handler)

