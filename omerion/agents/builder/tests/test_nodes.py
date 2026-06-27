# omerion/agents/builder/tests/test_nodes.py
"""Unit tests for BUILDER (Agent #11) — all outside systems mocked."""
from __future__ import annotations

import pytest
from uuid import uuid4
from omerion_core.events.bus import EventType
from omerion_core.events.schemas import BuildTaskCompleted, BuildTaskFailed, EVENT_SCHEMAS
from omerion_core.settings import settings
from agents.builder.state import BuilderState, TaskResult


# ─── Event type and schema tests ──────────────────────────────────────────────

def test_build_task_failed_in_enum():
    assert EventType.BUILD_TASK_FAILED.value == "build.task.failed"


def test_build_task_completed_schema_registered():
    assert "build.task.completed" in EVENT_SCHEMAS


def test_build_task_failed_schema_registered():
    assert "build.task.failed" in EVENT_SCHEMAS


def test_build_task_completed_natural_key():
    dep_id = uuid4()
    e = BuildTaskCompleted(
        source_agent="builder",
        correlation_id=uuid4(),
        idempotency_key="test-key-123",
        deployment_id=dep_id,
        task_slug="add-auth",
        pr_url="https://github.com/org/repo/pull/42",
        pr_number=42,
    )
    assert "add-auth" in e.natural_key
    assert str(dep_id) in e.natural_key


def test_build_task_failed_carries_reason():
    e = BuildTaskFailed(
        source_agent="builder",
        correlation_id=uuid4(),
        idempotency_key="test-key-456",
        deployment_id=uuid4(),
        task_slug="add-auth",
        failure_reason="pytest: 3/3 attempts failed",
    )
    assert e.failure_reason


# ─── Config tests ──────────────────────────────────────────────────────────────

def test_builder_config_in_agents_yaml():
    cfg = settings.agent("builder")
    assert cfg["max_retries"] == 3
    assert "test_command" in cfg


def test_builder_state_defaults():
    s = BuilderState(run_id=uuid4(), blueprint_id=uuid4(), deployment_id=uuid4())
    assert s.agent_name == "builder"
    assert s.tasks == []
    assert s.failed_slugs == []


def test_task_result_tracks_attempts():
    tr = TaskResult(slug="add-auth", task_id=uuid4(), branch_name="feat/x")
    assert tr.attempts == 0
    assert tr.pr_url is None


# ─── Failure-escalation routing (actionable retry / abandon) ──────────────────

from unittest.mock import patch

from agents.builder.graph import _after_escalation, _has_failures, hitl_wait_node

_GP = "agents.builder.graph."


def _builder_state():
    return BuilderState(run_id=uuid4(), session_id="sess-1",
                        blueprint_id=uuid4(), deployment_id=uuid4())


def test_has_failures_escalates_while_retry_budget_remains():
    s = _builder_state()
    s.tasks = [TaskResult(slug="a", task_id=uuid4(), branch_name="b", status="failed")]
    assert _has_failures(s) == "hitl_escalate"


def test_has_failures_finalizes_when_budget_spent():
    s = _builder_state()
    s.tasks = [TaskResult(slug="a", task_id=uuid4(), branch_name="b", status="failed")]
    s.founder_retry_count = settings.agent("builder").get("max_founder_retries", 1)
    assert _has_failures(s) == "emit_summary"


def test_has_failures_finalizes_when_no_failures():
    s = _builder_state()
    s.tasks = [TaskResult(slug="a", task_id=uuid4(), branch_name="b", status="pr_open")]
    assert _has_failures(s) == "emit_summary"


def test_hitl_wait_approve_requests_retry_and_increments():
    s = _builder_state()
    s.builder_hitl_review_id = uuid4()
    with patch(_GP + "interrupt",
               return_value={"decisions": {str(s.builder_hitl_review_id): "approved"}}):
        out = hitl_wait_node(s)
    assert out.retry_requested is True
    assert out.founder_retry_count == 1
    assert out.builder_hitl_review_id is None   # cleared for a fresh escalation


def test_hitl_wait_reject_abandons():
    s = _builder_state()
    s.builder_hitl_review_id = uuid4()
    with patch(_GP + "interrupt",
               return_value={"decisions": {str(s.builder_hitl_review_id): "rejected"}}):
        out = hitl_wait_node(s)
    assert out.retry_requested is False
    assert out.founder_retry_count == 0


def test_after_escalation_routes_on_retry_flag():
    s = _builder_state()
    s.retry_requested = True
    assert _after_escalation(s) == "execute_builds"
    s.retry_requested = False
    assert _after_escalation(s) == "emit_summary"


# ─── Tool tests ───────────────────────────────────────────────────────────────

from agents.builder.tools import (
    build_acceptance_criteria_md,
    check_banned_paths,
    check_secret_patterns,
    extract_json_file_changes,
)


def test_extract_json_file_changes_from_prose():
    raw = 'Sure! Here you go: [{"path": "foo.py", "content": "x=1"}] enjoy!'
    result = extract_json_file_changes(raw)
    assert result == [{"path": "foo.py", "content": "x=1"}]


def test_extract_json_file_changes_returns_empty_on_garbage():
    assert extract_json_file_changes("no json here") == []


def test_check_secret_patterns_catches_api_key():
    changes = [{"path": "foo.py", "content": 'token = "sk-ant-supersecret"'}]
    assert check_secret_patterns(changes, ["sk-ant-"]) is True


def test_check_secret_patterns_passes_clean_code():
    changes = [{"path": "foo.py", "content": "x = os.environ['TOKEN']"}]
    assert check_secret_patterns(changes, ["sk-ant-"]) is False


def test_check_banned_paths_blocks_workflow():
    changes = [{"path": ".github/workflows/deploy.yml", "content": "..."}]
    assert check_banned_paths(changes, [".github/workflows/"]) is True


def test_check_banned_paths_allows_normal_file():
    changes = [{"path": "omerion/agents/builder/tools.py", "content": "..."}]
    assert check_banned_paths(changes, [".github/workflows/"]) is False


def test_build_acceptance_criteria_md():
    criteria = ["Feature works end-to-end", "Unit tests pass"]
    md = build_acceptance_criteria_md(criteria)
    assert "- [ ] Feature works end-to-end" in md
    assert "- [ ] Unit tests pass" in md


def test_build_acceptance_criteria_md_empty_uses_default():
    md = build_acceptance_criteria_md([])
    assert "- [ ]" in md


def test_author_pr_body_is_deterministic():
    from agents.builder.tools import author_pr_body
    task = TaskResult(
        slug="add-auth",
        task_id=uuid4(),
        branch_name="feat/x",
        title="Add JWT auth",
        rationale="Security requirement",
        acceptance_criteria=["Auth works", "Tests pass"],
        attempts=2,
    )
    body = author_pr_body(None, task, [{"path": "auth.py", "content": ""}], "uv run pytest")
    assert "Add JWT auth" in body
    assert "- [ ] Auth works" in body
    assert "attempt 2 of 3" in body
    assert "auth.py" in body


def test_commit_changes_reraises_rate_limit():
    from agents.builder.tools import commit_changes
    from github import GithubException

    rate_limit_exc = GithubException(403, {"message": "API rate limit exceeded"}, {})

    with patch("agents.builder.tools._repo") as mock_repo:
        mock_gh = MagicMock()
        mock_gh.get_contents.side_effect = rate_limit_exc
        mock_repo.return_value = mock_gh

        with pytest.raises(GithubException) as exc_info:
            commit_changes([{"path": "foo.py", "content": "x=1"}], "feat/x", "test")
        assert exc_info.value.status == 403


# ─── Graph node tests ─────────────────────────────────────────────────────────

from unittest.mock import MagicMock, patch
from agents.builder.graph import emit_summary_node, load_tasks_node


@pytest.fixture
def bstate():
    return BuilderState(
        run_id=uuid4(),
        blueprint_id=uuid4(),
        deployment_id=uuid4(),
    )


def test_load_tasks_populates_state(bstate):
    dep_id = bstate.deployment_id
    raw_tasks = [
        {
            "task_id": str(uuid4()),
            "slug": "add-auth",
            "title": "Add JWT auth",
            "acceptance_criteria": ["auth works"],
            "rationale": "security",
            "branch_name": "feat/internal/omerion-internal/add-auth",
            "status": "branch_open",
        }
    ]
    bstate.scratch["event_payload"] = {
        "deployment_id": str(dep_id),
        "blueprint_id": str(bstate.blueprint_id),
        "tasks": [{"slug": "add-auth", "phase": "phase_1", "title": "Add JWT auth"}],
    }
    with patch("agents.builder.graph.load_full_tasks", return_value=raw_tasks):
        out = load_tasks_node(bstate)
    assert len(out.tasks) == 1
    assert out.tasks[0].slug == "add-auth"
    assert out.tasks[0].branch_name == "feat/internal/omerion-internal/add-auth"


def test_load_tasks_skips_tasks_without_branch(bstate):
    dep_id = bstate.deployment_id
    raw_tasks = [
        {
            "task_id": str(uuid4()),
            "slug": "no-branch",
            "title": "Task without branch",
            "acceptance_criteria": [],
            "rationale": "",
            "branch_name": "",
            "status": "pending",
        }
    ]
    bstate.scratch["event_payload"] = {
        "deployment_id": str(dep_id),
        "blueprint_id": str(bstate.blueprint_id),
        "tasks": [{"slug": "no-branch", "phase": "phase_1", "title": "x"}],
    }
    with patch("agents.builder.graph.load_full_tasks", return_value=raw_tasks):
        out = load_tasks_node(bstate)
    assert out.tasks == []


def test_emit_summary_marks_failed_slugs(bstate):
    tr = TaskResult(
        slug="add-auth",
        task_id=uuid4(),
        branch_name="feat/x",
        status="failed",
        notes="timeout",
        attempts=3,
    )
    bstate.tasks = [tr]
    with patch("agents.builder.graph.emit_event"), \
         patch("agents.builder.graph.update_task_status"):
        out = emit_summary_node(bstate)
    assert "add-auth" in out.failed_slugs


def test_emit_summary_emits_completed_for_pr_open_tasks(bstate):
    from omerion_core.events.bus import EventType
    tr = TaskResult(
        slug="add-auth",
        task_id=uuid4(),
        branch_name="feat/x",
        status="pr_open",
        pr_url="https://github.com/org/repo/pull/5",
        pr_number=5,
        attempts=1,
    )
    bstate.tasks = [tr]
    with patch("agents.builder.graph.emit_event") as mock_emit, \
         patch("agents.builder.graph.update_task_status"):
        emit_summary_node(bstate)
    mock_emit.assert_called_once()
    call_args = mock_emit.call_args
    assert call_args.args[0] == EventType.BUILD_TASK_COMPLETED
