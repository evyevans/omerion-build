"""LinkedIn Cold & Warm Outreach (Agent #4).

Wave 1.9 migration: registers a wrapper contract (`contracts.py`) so that
`agent_wrapper.run("linkedin-outreach", ...)` does cohort opt-out
filtering, style-guard hard filtering on drafts, and recipient
verification before any LinkedIn DM goes out.
"""
from omerion_core.runtime.registry import register

# Side-effect: registers the AgentContract with the wrapper. Must happen
# before the first wrapper.run() call. The agents package __init__ imports
# this module, so registration occurs at app startup.
from . import contracts  # noqa: F401
from .graph import build as _build

register("linkedin-outreach", runtime="langgraph", handler=_build())
