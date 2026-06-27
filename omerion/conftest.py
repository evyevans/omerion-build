"""Root conftest — patches that must apply before any test module is collected.

The Anthropic SDK's httpx dependency changed its Client.__init__ signature;
`proxies` was removed. This shim prevents that TypeError from breaking test
collection for any module that instantiates ClaudeRouter at import time.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True, scope="session")
def _patch_anthropic_proxies():
    """Suppress the httpx proxies TypeError so tests can collect normally."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='{"ok": true}')],
        usage=MagicMock(input_tokens=10, output_tokens=5),
    )
    with patch("anthropic.Anthropic", return_value=mock_client):
        yield
