"""Unit tests for Build Orchestrator nodes — all outside systems mocked.

Post-pivot: the merge step was split out of the build fan-out and moved AFTER the
G3 gate, so nothing reaches `main` (the deploy trigger) without founder approval.
`execute_tasks` → `build_tasks` (stops at ci_pass) + `merge_tasks` (post-approval);
`hitl_deploy_review`/`hitl_deploy_wait` → one `hitl_gate` via the global policy.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.build_orchestrator.graph import (
    build_tasks_node,
    decompose_node,
    finalize_deployment_node,
    hitl_gate_node,
    merge_tasks_node,
)
from agents.build_orchestrator.state import BuildState, TaskSpec
from agents.build_orchestrator.tools import _enforce_granularity, _extract_json_array
from omerion_core.hitl.policy import Gate

P = "agents.build_orchestrator.graph."


@pytest.fixture
def state():
    return BuildState(
        run_id=uuid4(),
        session_id="sess-1",
        blueprint_id=uuid4(),
        client_slug="acme",
        repo_full_name="omerion/omerion-build",
    )


def _ci_pass_task(slug="a", pr=1):
    return TaskSpec(slug=slug, title=slug.upper(), phase="phase_1", rationale="r",
                    acceptance_criteria=["ac"], status="ci_pass", pr_number=pr, pr_url=f"u/{pr}")


def test_extract_json_array_from_noise():
    raw = "Here you go: [{\"slug\": \"a\"}, {\"slug\": \"b\"}]  — cheers"
    assert _extract_json_array(raw) == [{"slug": "a"}, {"slug": "b"}]


def test_extract_json_array_garbage_returns_empty():
    assert _extract_json_array("no brackets") == []


def test_granularity_fills_missing_acceptance():
    t = TaskSpec(slug="x", title="X", phase="phase_1", rationale="r", files_touched_estimate=2)
    _enforce_granularity([t], {"max_files_changed": 8, "must_have_acceptance_criteria": True})
    assert t.acceptance_criteria and "verified" in t.acceptance_criteria[0].lower()


def test_decompose_populates_tasks(state):
    state.scratch["blueprint"] = {"id": "bp-1", "w5h": {}, "ttwa": {}, "backlog": [], "constraints": {}}
    with patch(P + "ClaudeRouter") as R, patch(P + "decompose_blueprint") as dec:
        R.return_value = MagicMock()
        dec.return_value = [TaskSpec(slug="a", title="A", phase="phase_1", rationale="r",
                                     acceptance_criteria=["done"])]
        out = decompose_node(state)
    assert len(out.tasks) == 1


def test_build_tasks_marks_failed_on_exception(state):
    state.tasks = [TaskSpec(slug="a", title="A", phase="phase_1", rationale="r",
                            acceptance_criteria=["ac"], task_id=uuid4())]
    with patch(P + "create_issue", side_effect=RuntimeError("boom")), \
         patch(P + "update_task"):
        out = build_tasks_node(state)
    assert out.tasks[0].status == "failed"
    assert "boom" in out.tasks[0].notes


def test_build_tasks_does_not_merge(state):
    """The build fan-out must reach ci_pass and STOP — never merge (that's post-G3)."""
    state.tasks = [TaskSpec(slug="a", title="A", phase="phase_1", rationale="r",
                            acceptance_criteria=["ac"], task_id=uuid4())]
    with patch(P + "create_issue", return_value=1), patch(P + "create_branch", return_value="b"), \
         patch(P + "inject_to_cursor"), patch(P + "update_task"), \
         patch(P + "merge_pr") as merge, \
         patch(P + "poll_pr", return_value={"number": 1, "url": "u", "ci_status": "success"}):
        out = build_tasks_node(state)
    merge.assert_not_called()
    assert out.tasks[0].status == "ci_pass"


def test_hitl_gate_uses_g3_and_sets_approval(state):
    state.tasks = [_ci_pass_task()]
    with patch(P + "gate", return_value={"sess-1": "approved"}) as g:
        out = hitl_gate_node(state)
    g.assert_called_once()
    assert g.call_args.args[0] == Gate.DEPLOY_OR_INFRA
    assert out.deployment_approved is True


def test_hitl_gate_skipped_when_no_ci_pass(state):
    state.tasks = [TaskSpec(slug="a", title="A", phase="phase_1", rationale="r", status="ci_fail")]
    with patch(P + "gate") as g:
        out = hitl_gate_node(state)
    g.assert_not_called()
    assert out.deployment_approved is False


def test_merge_tasks_noop_when_unapproved(state):
    """THE G3 FIX: no merge to main without founder approval."""
    state.tasks = [_ci_pass_task()]
    state.deployment_approved = False
    with patch(P + "merge_pr") as merge, patch(P + "update_task"):
        out = merge_tasks_node(state)
    merge.assert_not_called()
    assert out.tasks[0].status == "ci_pass"  # NOT merged


def test_merge_tasks_merges_when_approved(state):
    state.tasks = [_ci_pass_task(pr=7)]
    state.deployment_approved = True
    with patch(P + "merge_pr", return_value=True) as merge, patch(P + "update_task"):
        out = merge_tasks_node(state)
    merge.assert_called_once_with(7)
    assert out.tasks[0].status == "merged"


def test_finalize_failed_on_rejection(state):
    state.deployment_id = uuid4()
    state.deployment_approved = False
    state.tasks = []
    with patch(P + "update_deployment") as upd:
        finalize_deployment_node(state)
    upd.assert_called_once()
    assert state.deployment_status == "failed"
