"""Client Onboarding — end-to-end provisioning for newly signed clients."""
from omerion_core.runtime.registry import register

from . import contracts  # noqa: F401
from .graph import build as _build

register("client-onboarding", runtime="langgraph", handler=_build())
