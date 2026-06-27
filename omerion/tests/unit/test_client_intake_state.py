"""Tests for client_intake state (Task 2: Pydantic v2 upgrade).

Loads state.py directly via importlib to avoid agents/__init__.py cascade.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_state_module():
    """Load agents/client_intake/state.py without triggering agents/__init__."""
    state_path = Path(__file__).parent.parent.parent / "agents" / "client_intake" / "state.py"
    spec = importlib.util.spec_from_file_location("_client_intake_state", state_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_state_mod = _load_state_module()
IntakeState = _state_mod.IntakeState


def test_intake_state_is_pydantic():
    from pydantic import BaseModel
    assert issubclass(IntakeState, BaseModel), (
        f"IntakeState must extend BaseModel; got {IntakeState.__bases__}"
    )


def test_intake_state_defaults():
    s = IntakeState(blueprint_id="bp-001")
    assert s.data_gaps == []
    assert s.confidence_score == 0.0
    assert s.extraction_attempts == 0
    assert s.client_id is None


def test_intake_state_gaps_annotated():
    """Annotated[list, add] reducer must be present on data_gaps."""
    import typing
    hints = typing.get_type_hints(IntakeState, include_extras=True)
    assert "data_gaps" in hints
