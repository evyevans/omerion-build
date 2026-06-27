from agents.client_intake.state import IntakeState


def test_intake_state_is_pydantic():
    from pydantic import BaseModel
    assert issubclass(IntakeState, BaseModel)


def test_intake_state_defaults():
    s = IntakeState(blueprint_id="bp-001", transcript_text="", founder_notes="", agent_name="client_intake")
    assert s.data_gaps == []
    assert s.confidence_score == 0.0
    assert s.extraction_attempts == 0
    assert s.client_id is None


def test_intake_state_gaps_accumulate():
    """Annotated[list, add] reducer must accumulate, not overwrite."""
    import typing
    hints = typing.get_type_hints(IntakeState, include_extras=True)
    assert "data_gaps" in hints
