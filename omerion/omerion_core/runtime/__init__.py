"""Omerion local runtime layer.

The three runtime modules here own:
    registry     — skill name → callable (LangGraph graph or Agent SDK handler)
    checkpointer — shared LangGraph PostgresSaver over Supabase (DATABASE_URL)
    scheduler    — APScheduler that reads `schedule:` from each .skill.md
"""
from omerion_core.runtime.registry import SkillHandler, get_handler, register, registered_skills
from omerion_core.runtime.checkpointer import get_checkpointer, resume_thread

__all__ = [
    "SkillHandler",
    "get_handler",
    "get_checkpointer",
    "register",
    "registered_skills",
    "resume_thread",
]
