from agents.validator.state import ValidatorState


def test_validator_state_defaults():
    s = ValidatorState(
        pr_url="https://github.com/x/y/pull/1",
        pr_number=1,
        repo_full="x/y",
    )
    assert s.verdict is None
    assert s.acceptance_criteria == []
    assert s.lint_errors == []
    assert s.agent_name == "validator"
    assert s.head_branch == ""
