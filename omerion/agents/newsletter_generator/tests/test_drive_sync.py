from __future__ import annotations

import pytest

from agents.newsletter_generator import drive_sync


@pytest.mark.parametrize(
    "filename, default_type, fallback_seq, expected",
    [
        ("real_estate__skill_pack__3.pdf", "skill_pack", 9, ("Real Estate", "skill_pack", 3)),
        ("general__playbook__1.pdf", "playbook", 9, ("General", "playbook", 1)),
        ("proptech__skills__week_2.pdf", "skill_pack", 9, ("Proptech", "skill_pack", 2)),
        # No convention → all defaults, NOT the bare filename as industry.
        ("random.pdf", "playbook", 4, ("General", "playbook", 4)),
        # Unknown type token falls back to the run's default type.
        ("saas__widget__5.pdf", "skill_pack", 9, ("Saas", "skill_pack", 5)),
    ],
)
def test_parse_filename(filename, default_type, fallback_seq, expected):
    assert drive_sync._parse_filename(filename, default_type, fallback_seq) == expected


def test_resolve_folder_precedence(monkeypatch):
    # Per-type folder wins over the shared fallback.
    monkeypatch.setattr(drive_sync.settings, "newsletter_skillpack_drive_folder_id", "PACK", raising=False)
    monkeypatch.setattr(drive_sync.settings, "newsletter_drive_folder_id", "SHARED", raising=False)
    assert drive_sync._resolve_folder_id("skillpack") == "PACK"


def test_resolve_folder_fallback(monkeypatch):
    monkeypatch.setattr(drive_sync.settings, "newsletter_playbook_drive_folder_id", "", raising=False)
    monkeypatch.setattr(drive_sync.settings, "newsletter_drive_folder_id", "SHARED", raising=False)
    assert drive_sync._resolve_folder_id("playbook") == "SHARED"


def test_resolve_folder_none(monkeypatch):
    monkeypatch.setattr(drive_sync.settings, "newsletter_skillpack_drive_folder_id", "", raising=False)
    monkeypatch.setattr(drive_sync.settings, "newsletter_drive_folder_id", "", raising=False)
    assert drive_sync._resolve_folder_id("skillpack") is None


def test_sync_unknown_mode_is_noop():
    # 'newsletter' mode has no Drive folder config → 0, no crash.
    assert drive_sync.sync_drive_materials("newsletter") == 0
