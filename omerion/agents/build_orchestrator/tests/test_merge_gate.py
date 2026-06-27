"""TDD tests for the VALIDATOR-gated merge_pr."""
from unittest.mock import MagicMock, patch


def test_merge_pr_blocked_without_validator_approval():
    """merge_pr must return False if VALIDATOR has not approved."""
    with patch("agents.build_orchestrator.tools.validator_approval_exists", return_value=False), \
         patch("agents.build_orchestrator.tools._repo") as mock_repo:

        mock_pr = MagicMock()
        mock_pr.mergeable = True
        mock_repo.return_value.get_pull.return_value = mock_pr

        from agents.build_orchestrator.tools import merge_pr
        result = merge_pr(pr_number=42, repo_full="owner/repo")

    assert result is False
    mock_pr.merge.assert_not_called()


def test_merge_pr_proceeds_with_validator_approval():
    """merge_pr must squash-merge when VALIDATOR has approved."""
    with patch("agents.build_orchestrator.tools.validator_approval_exists", return_value=True), \
         patch("agents.build_orchestrator.tools._repo") as mock_repo:

        mock_pr = MagicMock()
        mock_pr.mergeable = True
        mock_repo.return_value.get_pull.return_value = mock_pr

        from agents.build_orchestrator.tools import merge_pr
        result = merge_pr(pr_number=42, repo_full="owner/repo")

    assert result is True
    mock_pr.merge.assert_called_once_with(merge_method="squash")


def test_merge_pr_blocked_when_pr_not_mergeable():
    """merge_pr must respect GitHub's own mergeable flag even after VALIDATOR approval."""
    with patch("agents.build_orchestrator.tools.validator_approval_exists", return_value=True), \
         patch("agents.build_orchestrator.tools._repo") as mock_repo:

        mock_pr = MagicMock()
        mock_pr.mergeable = False
        mock_repo.return_value.get_pull.return_value = mock_pr

        from agents.build_orchestrator.tools import merge_pr
        result = merge_pr(pr_number=42, repo_full="owner/repo")

    assert result is False
    mock_pr.merge.assert_not_called()
