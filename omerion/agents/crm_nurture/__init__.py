"""CRM Warm Leads Nurture (Agent #5).

Wave 1.9 migration: wrapper contract. Email recipients carry the same
"AI cannot invent identities" guarantee as LinkedIn — the wrapper
rejects any contact_id in the output that wasn't in the filtered cohort.
"""
from omerion_core.runtime.registry import register

# Side-effect: registers AgentContract with the wrapper.
from . import contracts  # noqa: F401
from .graph import build as _build

register("crm-nurture", runtime="langgraph", handler=_build())
