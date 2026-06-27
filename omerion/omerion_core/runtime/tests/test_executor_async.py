"""Tests for execute_run_async timeout and outcome paths."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_execute_run_async_times_out_cleanly():
    """execute_run_async must mark the run 'failed' and return when asyncio.wait_for fires."""
    async def slow_agent(name, inputs):
        await asyncio.sleep(9999)

    with patch("omerion_core.runtime.run_executor.run_lifecycle") as mock_lc, \
         patch("omerion_core.runtime.run_executor.run_agent_by_name_async", side_effect=slow_agent), \
         patch("omerion_core.runtime.run_executor._run_cost_so_far", return_value=0.0), \
         patch("omerion_core.runtime.run_executor._notify_terminal"), \
         patch("omerion_core.runtime.run_executor._is_agent_paused", return_value=(False, None)), \
         patch("omerion_core.runtime.run_executor.log_run_start"), \
         patch("omerion_core.runtime.run_executor.log_run_failed"):

        mock_lc.get_run.return_value = {
            "run_id": "test-run",
            "agent_name": "test-agent",
            "inputs": {},
            "triggered_by": "test",
            "correlation_id": "test-run",
            "started_at": None,
        }
        mock_lc.mark_running.return_value = None
        mock_lc.fail_run.return_value = {"status": "failed", "run_id": "test-run"}
        mock_lc.mark_superseded.return_value = None
        mock_lc.transition.return_value = {}

        from omerion_core.runtime.run_executor import execute_run_async

        with patch("omerion_core.runtime.run_executor.AGENT_TIMEOUT_SECONDS", 0.05):
            result = await execute_run_async("test-run")

    assert mock_lc.fail_run.called
    call_kwargs = mock_lc.fail_run.call_args
    assert "timed out" in str(call_kwargs).lower() or "timeout" in str(call_kwargs).lower()
    assert result.get("status") == "failed"


@pytest.mark.asyncio
async def test_execute_run_async_completes_normally():
    """execute_run_async marks run 'completed' when agent returns status=completed."""
    async def fast_agent(name, inputs):
        return {"session_id": inputs.get("session_id", "x"), "status": "completed", "result": {"ok": True}}

    with patch("omerion_core.runtime.run_executor.run_lifecycle") as mock_lc, \
         patch("omerion_core.runtime.run_executor.run_agent_by_name_async", side_effect=fast_agent), \
         patch("omerion_core.runtime.run_executor._run_cost_so_far", return_value=0.01), \
         patch("omerion_core.runtime.run_executor._notify_terminal"), \
         patch("omerion_core.runtime.run_executor._is_agent_paused", return_value=(False, None)), \
         patch("omerion_core.runtime.run_executor.log_run_start"), \
         patch("omerion_core.runtime.run_executor.log_run_complete"):

        mock_lc.get_run.return_value = {
            "run_id": "run-ok",
            "agent_name": "fast-agent",
            "inputs": {},
            "triggered_by": "test",
            "correlation_id": "run-ok",
            "started_at": None,
        }
        mock_lc.mark_running.return_value = None
        mock_lc.complete_run.return_value = {"status": "completed", "run_id": "run-ok"}

        from omerion_core.runtime.run_executor import execute_run_async
        result = await execute_run_async("run-ok")

    assert mock_lc.complete_run.called
    assert result.get("status") == "completed"
