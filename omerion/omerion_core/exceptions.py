"""User-facing error types for agent runtime."""

from __future__ import annotations


class UserFacingError(Exception):
    """Raised when an agent fails with a message safe to show humans."""
