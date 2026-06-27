"""Tests for dossier asyncpg LISTEN/NOTIFY listener (Task 5)."""


def test_dossier_listener_importable():
    from omerion_core.events.dossier_listener import DossierListener
    assert DossierListener is not None


def test_dossier_listener_has_start_stop():
    from omerion_core.events.dossier_listener import DossierListener
    listener = DossierListener.__new__(DossierListener)
    assert callable(getattr(listener, "start", None)), "must have async start()"
    assert callable(getattr(listener, "stop", None)), "must have async stop()"
