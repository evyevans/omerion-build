"""Smoke-test that checkpointer async API is wired correctly."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch


async def test_cancel_thread_returns_false_when_no_saver():
    """cancel_thread must return False (not raise) when checkpointer is None."""
    from omerion_core.runtime.checkpointer import cancel_thread
    with patch("omerion_core.runtime.checkpointer.get_checkpointer", return_value=None):
        result = await cancel_thread("thread-1")
    assert result is False


async def test_cancel_thread_awaits_aget():
    """cancel_thread must call aget() (not the sync get()) on the async saver."""
    mock_saver = AsyncMock()
    mock_saver.aget = AsyncMock(return_value={"some": "state"})
    from omerion_core.runtime.checkpointer import cancel_thread
    with patch("omerion_core.runtime.checkpointer.get_checkpointer", return_value=mock_saver):
        result = await cancel_thread("thread-xyz")
    assert result is True
    mock_saver.aget.assert_awaited_once_with({"configurable": {"thread_id": "thread-xyz"}})


async def test_resume_thread_raises_when_no_saver():
    """resume_thread must raise RuntimeError (not TypeError) when checkpointer is None."""
    import pytest
    from omerion_core.runtime.checkpointer import resume_thread
    with patch("omerion_core.runtime.checkpointer.get_checkpointer", return_value=None):
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            await resume_thread("thread-1", resume_payload={"decision": "approved"})
