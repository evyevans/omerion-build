"""Tests for trainer GitHub commit idempotency via effect_log."""
from pathlib import Path
from unittest.mock import patch, MagicMock


def _commit_fn():
    from agents.trainer import tools
    return tools._commit_prompt_update_to_github


def test_second_call_skips_github_api():
    """If effect_log already has a record for this file+hash, no GitHub API call is made."""
    existing_log_row = {"result": {"sha": "abc123"}}

    with patch("agents.trainer.tools._supa" if False else "omerion_core.clients.supabase_client.supabase") as _unused, \
         patch("agents.trainer.tools.log"):
        # Patch at the import-site alias inside the function
        import agents.trainer.tools as tools_mod

        orig_supa = getattr(tools_mod, "_supa", None)

        mock_supa = MagicMock()
        mock_supa.table.return_value.select.return_value.eq.return_value \
            .maybe_single.return_value.execute.return_value.data = existing_log_row

        # Patch supabase inside the function's local import
        with patch("omerion_core.clients.supabase_client.supabase", mock_supa), \
             patch("omerion_core.clients.github_client.github_client") as mock_gh_fn:

            result = tools_mod._commit_prompt_update_to_github(
                agent_name="test-agent",
                prompt_constant_name="SYSTEM_PROMPT",
                file_path=Path("/tmp/fake/prompts.py"),
                new_content="new content here",
                diff_summary="SYSTEM_PROMPT: 100→110 chars",
                hitl_review_id="review-1",
            )

    # GitHub API must NOT have been called
    mock_gh_fn.assert_not_called()
    assert result["committed"] is True
    assert result["sha"] == "abc123"


def test_first_call_writes_effect_log_after_github():
    """First call must commit to GitHub and then write to effect_log."""
    import agents.trainer.tools as tools_mod

    mock_supa = MagicMock()
    # effect_log has no prior record — maybe_single returns None
    mock_supa.table.return_value.select.return_value.eq.return_value \
        .maybe_single.return_value.execute.return_value.data = None

    mock_repo = MagicMock()
    mock_repo.get_contents.return_value = MagicMock(sha="old-sha")
    commit_mock = MagicMock()
    commit_mock.sha = "new-sha"
    mock_repo.update_file.return_value = {"commit": commit_mock}

    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    mock_settings = MagicMock()
    mock_settings.github_build_repo = "owner/repo"

    # Patch settings at its import location in trainer tools AND at the module attribute
    with patch("omerion_core.clients.supabase_client.supabase", mock_supa), \
         patch("omerion_core.clients.github_client.github_client", return_value=mock_gh), \
         patch.object(tools_mod, "settings", mock_settings):

        result = tools_mod._commit_prompt_update_to_github(
            agent_name="test-agent",
            prompt_constant_name="SYSTEM_PROMPT",
            file_path=Path("/tmp/fake/prompts.py"),
            new_content="first content",
            diff_summary="SYSTEM_PROMPT: 50→70 chars",
            hitl_review_id="review-2",
        )

    # Must have inserted into effect_log
    insert_calls = [str(c) for c in mock_supa.table.call_args_list]
    effect_log_calls = [c for c in insert_calls if "effect_log" in c]
    assert len(effect_log_calls) > 0, "effect_log insert was not called"
    assert result["committed"] is True
    assert result["sha"] == "new-sha"
