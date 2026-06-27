"""Tests for the R3 coordination gate."""
import pytest
from unittest.mock import patch, MagicMock


def _mock_supabase_response(data):
    mock = MagicMock()
    mock.data = data
    mock.execute.return_value = mock
    return mock


def test_mark_complete_upserts_registry():
    """mark_agent_complete should upsert into agent_run_registry."""
    with patch("omerion_core.runtime.agent_coordinator.supabase") as mock_sb, \
         patch("omerion_core.runtime.agent_coordinator.check_r3_gate"):
        mock_sb.table.return_value.upsert.return_value.execute.return_value = _mock_supabase_response([])
        from omerion_core.runtime.agent_coordinator import mark_agent_complete
        mark_agent_complete("r1-market-tech-watcher")
        mock_sb.table.assert_called_with("agent_run_registry")


def test_check_r3_gate_fires_when_both_complete():
    """If R1 and R2 are both complete this week and R3 hasn't run, trigger R3."""
    r1_row = {"agent_id": "r1-market-tech-watcher", "status": "complete"}
    r2_row = {"agent_id": "r2-oss-scout", "status": "complete"}
    r3_empty = []

    with patch("omerion_core.runtime.agent_coordinator.supabase") as mock_sb, \
         patch("omerion_core.runtime.agent_coordinator._trigger_r3") as mock_trigger:
        def table_side_effect(name):
            mock_tbl = MagicMock()
            if name == "agent_run_registry":
                # Chain for prerequisites query: .select().eq().eq().in_().execute()
                prereq_chain = MagicMock()
                prereq_chain.execute.return_value = _mock_supabase_response([r1_row, r2_row])
                # Chain for R3 status query: .select().eq().eq().eq().execute()
                r3_chain = MagicMock()
                r3_chain.execute.return_value = _mock_supabase_response(r3_empty)

                # First call to .select() goes to prerequisites, second to R3 check
                call_count = [0]
                def select_side(*a, **kw):
                    call_count[0] += 1
                    if call_count[0] == 1:
                        m = MagicMock()
                        m.eq.return_value = m
                        m.in_.return_value = prereq_chain
                        return m
                    else:
                        m = MagicMock()
                        m.eq.return_value = m
                        m.execute.return_value = _mock_supabase_response(r3_empty)
                        return m

                mock_tbl.select.side_effect = select_side
            return mock_tbl

        mock_sb.table.side_effect = table_side_effect

        from omerion_core.runtime import agent_coordinator
        agent_coordinator.check_r3_gate()
        mock_trigger.assert_called_once()


def test_check_r3_gate_does_not_fire_if_r3_already_ran():
    """If R3 already has status=complete this week, gate must NOT re-trigger."""
    r1_row = {"agent_id": "r1-market-tech-watcher", "status": "complete"}
    r2_row = {"agent_id": "r2-oss-scout", "status": "complete"}
    r3_row = [{"status": "complete"}]

    with patch("omerion_core.runtime.agent_coordinator.supabase") as mock_sb, \
         patch("omerion_core.runtime.agent_coordinator._trigger_r3") as mock_trigger:
        call_count = [0]

        def table_side_effect(name):
            mock_tbl = MagicMock()
            if name == "agent_run_registry":
                def select_side(*a, **kw):
                    call_count[0] += 1
                    m = MagicMock()
                    m.eq.return_value = m
                    if call_count[0] == 1:
                        m.in_.return_value = MagicMock(
                            execute=lambda: _mock_supabase_response([r1_row, r2_row])
                        )
                    else:
                        m.execute.return_value = _mock_supabase_response(r3_row)
                    return m
                mock_tbl.select.side_effect = select_side
            return mock_tbl

        mock_sb.table.side_effect = table_side_effect

        from omerion_core.runtime import agent_coordinator
        agent_coordinator.check_r3_gate()
        mock_trigger.assert_not_called()
