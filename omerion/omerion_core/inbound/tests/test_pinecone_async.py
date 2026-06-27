"""Verify the async Pinecone surface is correctly wired."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def test_get_async_index_returns_none_before_init():
    """get_async_index() must return None when lifespan has not run."""
    from omerion_core.clients import pinecone_client
    pinecone_client._async_index = None
    from omerion_core.clients.pinecone_client import get_async_index
    assert get_async_index() is None


async def test_query_outreach_signals_returns_empty_when_no_index():
    """query_outreach_signals must return '' (not raise) when index is None."""
    from omerion_core.clients import pinecone_client
    pinecone_client._async_index = None
    from omerion_core.outreach.signals import query_outreach_signals
    result = await query_outreach_signals("founder", "discovery")
    assert result == ""


async def test_query_outreach_signals_calls_async_query():
    """query_outreach_signals must await idx.query(), not call sync .query()."""
    import asyncio as asyncio_mod
    mock_idx = AsyncMock()
    mock_idx.query = AsyncMock(return_value=MagicMock(matches=[]))
    from omerion_core.clients import pinecone_client
    pinecone_client._async_index = mock_idx

    with patch("omerion_core.outreach.signals.asyncio") as mock_asyncio:
        mock_asyncio.to_thread = AsyncMock(return_value=[0.1] * 512)
        from omerion_core.outreach.signals import query_outreach_signals
        result = await query_outreach_signals("founder", "discovery")

    mock_idx.query.assert_awaited_once()
    assert result == ""
    pinecone_client._async_index = None
