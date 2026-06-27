"""Unit tests for VALIDATOR tools — pure functions only (no network/DB)."""
from agents.validator.tools import chunk_diff_by_file, lint_diff


def test_lint_diff_catches_console_log():
    patch = "+  console.log('debug')\n+  const x = 1;"
    errors = lint_diff(patch, changed_files=["src/app.ts"])
    assert any("console.log" in e for e in errors)


def test_lint_diff_catches_bare_print():
    patch = "+print('debug value')\n+x = 1"
    errors = lint_diff(patch, changed_files=["app.py"])
    assert any("bare print()" in e for e in errors)


def test_lint_diff_requires_test_file():
    patch = "+x = 1\n"
    errors = lint_diff(patch, changed_files=["src/feature.py"])
    assert any("test" in e.lower() for e in errors)


def test_lint_diff_passes_when_test_present():
    patch = "+x = 1\n"
    errors = lint_diff(patch, changed_files=["src/feature.py", "tests/test_feature.py"])
    assert not any("console.log" in e or "bare print()" in e for e in errors)


def test_lint_diff_ignores_removed_lines():
    # Lines prefixed with '-' are deletions — should not trigger lint
    patch = "-  console.log('old debug')\n+  // removed"
    errors = lint_diff(patch, changed_files=["src/app.ts", "tests/test_app.ts"])
    assert not any("console.log" in e for e in errors)


# ─── chunk_diff_by_file tests ──────────────────────────────────────────────────

def test_chunk_diff_by_file_splits_correctly():
    patch = "--- a/foo.py\n+++ b/foo.py\n+x=1\n--- a/bar.py\n+++ b/bar.py\n+y=2\n"
    chunks = chunk_diff_by_file(patch)
    assert len(chunks) == 2
    assert any("foo.py" in c for c in chunks)
    assert any("bar.py" in c for c in chunks)


def test_chunk_diff_by_file_caps_at_ten():
    files = [f"--- a/f{i}.py\n+++ b/f{i}.py\n+x={i}\n" for i in range(15)]
    patch = "".join(files)
    chunks = chunk_diff_by_file(patch)
    assert len(chunks) == 10


def test_chunk_diff_by_file_empty_returns_empty():
    assert chunk_diff_by_file("") == []


# ─── rejection_limit_exceeded tests ───────────────────────────────────────────

from unittest.mock import patch
from uuid import uuid4


def test_rejection_limit_exceeded_false_initially():
    uid = uuid4()
    with patch("agents.validator.tools.supabase") as mock_sb:
        mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"rejection_count": 0}]
        from agents.validator.tools import rejection_limit_exceeded
        assert rejection_limit_exceeded(uid) is False


def test_rejection_limit_exceeded_true_at_max():
    uid = uuid4()
    with patch("agents.validator.tools.supabase") as mock_sb:
        mock_sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{"rejection_count": 3}]
        from agents.validator.tools import rejection_limit_exceeded
        assert rejection_limit_exceeded(uid) is True
