"""Test that start_scheduler configures a persistent jobstore when DATABASE_URL is set."""
import pytest
from unittest.mock import patch, MagicMock


def test_scheduler_uses_sqlalchemy_jobstore_when_db_url_set(tmp_path):
    """When DATABASE_URL is set, scheduler must use SQLAlchemyJobStore, not MemoryJobStore."""
    from apscheduler.jobstores.memory import MemoryJobStore

    captured_jobstores = {}

    def capture_scheduler(jobstores=None, **kwargs):
        captured_jobstores.update(jobstores or {})
        mock = MagicMock()
        mock.get_jobs.return_value = []
        mock.start.return_value = None
        mock.add_job.return_value = None
        return mock

    mock_settings = MagicMock()
    mock_settings.database_url = "postgresql://user:pw@host/omerion"
    mock_settings.agent.return_value = {}

    mock_jobstore = MagicMock(spec=MemoryJobStore.__class__)

    with patch("omerion_core.runtime.scheduler.AsyncIOScheduler", side_effect=capture_scheduler), \
         patch("omerion_core.runtime.scheduler.settings", mock_settings), \
         patch("omerion_core.runtime.scheduler.SKILLS_DIR", tmp_path), \
         patch("apscheduler.jobstores.sqlalchemy.SQLAlchemyJobStore", return_value=mock_jobstore):
        from omerion_core.runtime.scheduler import start_scheduler
        start_scheduler(skills_dir=tmp_path)

    assert "default" in captured_jobstores
    assert not isinstance(captured_jobstores["default"], MemoryJobStore)


def test_scheduler_falls_back_to_memory_when_no_db_url(tmp_path):
    """When DATABASE_URL is empty, scheduler must still start (no crash)."""
    captured_jobstores = {}

    def capture_scheduler(jobstores=None, **kwargs):
        captured_jobstores.update(jobstores or {})
        mock = MagicMock()
        mock.get_jobs.return_value = []
        mock.start.return_value = None
        mock.add_job.return_value = None
        return mock

    mock_settings = MagicMock()
    mock_settings.database_url = ""
    mock_settings.agent.return_value = {}

    with patch("omerion_core.runtime.scheduler.AsyncIOScheduler", side_effect=capture_scheduler), \
         patch("omerion_core.runtime.scheduler.settings", mock_settings), \
         patch("omerion_core.runtime.scheduler.SKILLS_DIR", tmp_path):
        from omerion_core.runtime.scheduler import start_scheduler
        result = start_scheduler(skills_dir=tmp_path)
        assert result is not None

    assert "default" not in captured_jobstores
