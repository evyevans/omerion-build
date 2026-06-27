"""DEPLOYER — Infrastructure Provisioner (Agentic Factory Agent #18).

Consumes: deployment.live
Emits:    deployment.health_confirmed | deployment.health_failed

Pipeline: backup → migrate → provision → dns → smoke_test → (rollback?) → emit
"""
from omerion_core.runtime.registry import register

from .graph import build as _build

register("deployer", runtime="langgraph", handler=_build())
