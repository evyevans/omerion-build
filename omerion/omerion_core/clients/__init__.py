from omerion_core.clients.supabase_client import supabase
from omerion_core.clients.pinecone_client import pinecone_index
from omerion_core.clients.google_client import (
    gmail_service,
    calendar_service,
    drive_service,
    sheets_client,
    slides_service,
)
from omerion_core.clients.elevenlabs_client import elevenlabs_client
from omerion_core.clients.fireflies_client import fireflies_client
from omerion_core.clients.github_client import github_client

try:
    from omerion_core.clients.base44_client import patch_blueprint_request
except ImportError:
    def patch_blueprint_request(*args, **kwargs):
        raise RuntimeError("base44_client is not available in this deployment")

__all__ = [
    "supabase",
    "pinecone_index",
    "gmail_service",
    "calendar_service",
    "drive_service",
    "sheets_client",
    "slides_service",
    "elevenlabs_client",
    "fireflies_client",
    "github_client",
    "patch_blueprint_request",
]
