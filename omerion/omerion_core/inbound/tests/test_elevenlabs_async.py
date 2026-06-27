"""Verify ElevenLabs client returns the async variant."""
from __future__ import annotations

import importlib
from unittest.mock import patch


def test_elevenlabs_client_is_async():
    """elevenlabs_client() must return AsyncElevenLabs, not ElevenLabs."""
    from elevenlabs.client import AsyncElevenLabs
    # Use importlib to get the module, not the re-exported function from __init__
    mod = importlib.import_module("omerion_core.clients.elevenlabs_client")
    mod.elevenlabs_client.cache_clear()
    with patch.object(mod, "settings") as mock_settings:
        mock_settings.elevenlabs_api_key = "test-key"
        client = mod.elevenlabs_client()
        assert isinstance(client, AsyncElevenLabs), (
            f"Expected AsyncElevenLabs but got {type(client).__name__}"
        )
    mod.elevenlabs_client.cache_clear()
