"""Tests for HITL-aware sweeper stuck-run detection."""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock


def _make_run(run_id: str, started_at_offset_min: int, hitl_expires_at=None):
    now = datetime.now(timezone.utc)
    return {
        "run_id": run_id,
        "agent_name": "test-agent",
        "started_at": (now - timedelta(minutes=started_at_offset_min)).isoformat(),
        "hitl_expires_at": hitl_expires_at,
    }


def _mock_supabase_for_running(mock_supa, rows):
    """Wire mock_supa so the stuck_running query returns `rows`."""
    # The sweeper calls: supabase.table(...).select(...).eq(...).lt(...).limit(...).execute()
    # We need the chain to return .data = rows for the first call (running cohort)
    # and .data = [] for the second (hitl cohort).
    call_count = {"n": 0}
    running_chain = MagicMock()
    running_chain.execute.return_value.data = rows
    hitl_chain = MagicMock()
    hitl_chain.execute.return_value.data = []

    def _table_side_effect(name):
        t = MagicMock()
        t.select.return_value.eq.return_value.lt.return_value.limit.return_value = (
            running_chain if call_count["n"] == 0 else hitl_chain
        )
        call_count["n"] += 1
        return t

    mock_supa.table.side_effect = _table_side_effect


def test_sweeper_skips_run_with_future_hitl_expires_at():
    """Running run >30min with hitl_expires_at in the future must NOT be closed."""
    future_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    run = _make_run("run-1", started_at_offset_min=40, hitl_expires_at=future_expiry)

    with patch("omerion_core.runtime.sweeper.supabase") as mock_supa, \
         patch("omerion_core.runtime.sweeper._close_stuck") as mock_close, \
         patch("omerion_core.runtime.sweeper._alert_mission_control"):
        _mock_supabase_for_running(mock_supa, [run])
        from omerion_core.runtime.sweeper import sweep_stuck_runs
        result = sweep_stuck_runs()

    mock_close.assert_not_called()
    assert result["closed_running"] == 0


def test_sweeper_closes_run_without_hitl_expires_at():
    """Running run >30min with no hitl_expires_at MUST be closed."""
    run = _make_run("run-2", started_at_offset_min=40, hitl_expires_at=None)

    with patch("omerion_core.runtime.sweeper.supabase") as mock_supa, \
         patch("omerion_core.runtime.sweeper._close_stuck") as mock_close, \
         patch("omerion_core.runtime.sweeper._alert_mission_control"):
        _mock_supabase_for_running(mock_supa, [run])
        from omerion_core.runtime.sweeper import sweep_stuck_runs
        result = sweep_stuck_runs()

    mock_close.assert_called_once_with("run-2", "test-agent", reason="running_timeout")
    assert result["closed_running"] == 1


def test_sweeper_closes_run_with_past_hitl_expires_at():
    """Running run with hitl_expires_at already passed MUST be closed (HITL never fired)."""
    past_expiry = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    run = _make_run("run-3", started_at_offset_min=40, hitl_expires_at=past_expiry)

    with patch("omerion_core.runtime.sweeper.supabase") as mock_supa, \
         patch("omerion_core.runtime.sweeper._close_stuck") as mock_close, \
         patch("omerion_core.runtime.sweeper._alert_mission_control"):
        _mock_supabase_for_running(mock_supa, [run])
        from omerion_core.runtime.sweeper import sweep_stuck_runs
        result = sweep_stuck_runs()

    mock_close.assert_called_once()
    assert result["closed_running"] == 1


def test_sweeper_mixed_batch():
    """Batch with one skippable and one closeable run behaves independently."""
    future_expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    runs = [
        _make_run("run-safe", started_at_offset_min=45, hitl_expires_at=future_expiry),
        _make_run("run-stuck", started_at_offset_min=45, hitl_expires_at=None),
    ]

    with patch("omerion_core.runtime.sweeper.supabase") as mock_supa, \
         patch("omerion_core.runtime.sweeper._close_stuck") as mock_close, \
         patch("omerion_core.runtime.sweeper._alert_mission_control"):
        _mock_supabase_for_running(mock_supa, runs)
        from omerion_core.runtime.sweeper import sweep_stuck_runs
        result = sweep_stuck_runs()

    assert result["closed_running"] == 1
    closed_ids = [c.args[0] for c in mock_close.call_args_list]
    assert "run-stuck" in closed_ids
    assert "run-safe" not in closed_ids
