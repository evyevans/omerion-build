"""Tests for SECURITY_AUDITOR deterministic scan tools."""
import tempfile
from pathlib import Path
from agents.security_auditor.tools import scan_env_for_secrets
from agents.security_auditor.state import SecurityFinding


def test_scan_env_detects_anthropic_key():
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=ant-abc123def456ghi789jkl012mno345pqr678stu\n")
        findings = scan_env_for_secrets(tmpdir)
    assert len(findings) == 1
    assert findings[0].finding_type == "secret"
    assert findings[0].severity == "critical"


def test_scan_env_skips_example_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env.example"
        env_file.write_text("ANTHROPIC_API_KEY=ant-abc123def456ghi789jkl012mno345pqr678stu\n")
        findings = scan_env_for_secrets(tmpdir)
    assert len(findings) == 0


def test_scan_env_clean_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"
        env_file.write_text("SOME_VAR=safe_value\nANOTHER_VAR=also_safe\n")
        findings = scan_env_for_secrets(tmpdir)
    assert len(findings) == 0


def test_security_finding_model():
    f = SecurityFinding(
        finding_type="secret",
        severity="critical",
        resource=".env",
        description="test finding",
    )
    assert f.severity == "critical"
    assert f.cve_id is None
    assert f.remediation is None


def test_security_finding_with_cve():
    f = SecurityFinding(
        finding_type="dependency_cve",
        severity="high",
        resource="requests==2.28.0",
        description="CVE-2023-12345: path traversal",
        cve_id="CVE-2023-12345",
        remediation="Upgrade to 2.31.0",
    )
    assert f.cve_id == "CVE-2023-12345"
    assert f.finding_type == "dependency_cve"
