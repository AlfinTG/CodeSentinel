"""Phase 3 Gate Tests: Isolated Runner, Secret Redaction, Evidence Bounding, and Regression Fixture."""

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models.evidence import EvidenceItem
from app.models.project import Project
from app.models.run import CheckRun, Run, TestOutcome
from app.models.user import User
from app.services.normalizer import MAX_EVIDENCE_BYTES, normalize_and_bound_output
from app.services.redaction import redact_secrets
from app.services.runner import (
    CommandSecurityViolationError,
    execute_isolated_check,
    execute_run_checks,
    sanitize_runner_environment,
    validate_check_command,
)


def test_secret_redaction_engine():
    """Verify credentials, tokens, private keys, and canary strings are redacted."""
    raw_output = (
        "Error in API call: ghp_1234567890abcdefghijklmnopqrstuvwxyz "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.secret "
        "Connecting to postgresql://user:super_secret_pw@localhost:5432/db "
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...\n-----END RSA PRIVATE KEY-----\n"
        "canary_token=CANARY_SECRET_VALUE_999"
    )

    redacted = redact_secrets(raw_output, custom_canaries=["CANARY_SECRET_VALUE_999"])

    assert "ghp_1234567890" not in redacted
    assert "[REDACTED_GITHUB_TOKEN]" in redacted
    assert "[REDACTED_PASSWORD]" in redacted
    assert "[REDACTED_PRIVATE_KEY_BLOCK]" in redacted
    assert "CANARY_SECRET_VALUE_999" not in redacted
    assert "[REDACTED_CANARY]" in redacted


def test_evidence_bounding_at_256_kib():
    """Verify evidence items exceeding 256 KiB are truncated with an explicit notice."""
    oversized_text = "AssertionError: line failure\n" + ("x" * 300000)
    normalized = normalize_and_bound_output(oversized_text, source_label="unit_tests")

    assert normalized.truncated is True
    assert normalized.byte_length <= MAX_EVIDENCE_BYTES
    assert "[TRUNCATED:" in normalized.content_redacted


def test_unapproved_command_and_shell_injection_rejected():
    """Security Invariant: Rejects unapproved binaries and shell chaining metacharacters."""
    # Arbitrary binary rejected
    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["curl", "http://attacker.com"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["rm", "-rf", "/"])

    # Shell chaining injection rejected
    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["pytest", "test.py", ";", "cat", "/etc/passwd"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["pytest", "test.py && echo owned"])

    # Approved array passes validation
    validate_check_command(["pytest", "tests/unit", "-v"])


def test_runner_environment_isolation(monkeypatch):
    """Security Invariant: Runner process environment never receives application secrets."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/db")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "super-secret-github-key")
    monkeypatch.setenv("NEBIUS_TOKEN_FACTORY_API_KEY", "nebius-live-secret")

    clean_env = sanitize_runner_environment()

    assert "DATABASE_URL" not in clean_env
    assert "GITHUB_APP_CLIENT_SECRET" not in clean_env
    assert "NEBIUS_TOKEN_FACTORY_API_KEY" not in clean_env


def test_execution_timeout_enforcement():
    """Verify that checks exceeding the wall-clock timeout return status 'timed_out'."""
    # Run a sleep command that exceeds a tiny 1-second timeout without forbidden shell metacharacters
    command = ["python", "-c", "__import__('time').sleep(3)"]
    result = execute_isolated_check(
        check_key="timeout_test",
        check_name="Timeout Test",
        command=command,
        cwd=".",
        timeout_seconds=1,
    )

    assert result.status == "timed_out"
    assert result.failure_code == "check_timed_out"
    assert "[TIMEOUT:" in result.output


def test_controlled_regression_fixture_produces_deterministic_failure(
    db: Session,
    user_alice: User,
):
    """Gate 3 Acceptance: Deliberate regression produces deterministic failure and visible evidence."""
    fixture_dir = Path(__file__).parent / "fixtures" / "sample_repo"

    # Setup project and run
    project = Project(
        owner_user_id=user_alice.id,
        github_installation_id=987654,
        repository_id=123123,
        repository_full_name="org/sample-regression",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()

    run = Run(
        project_id=project.id,
        head_sha="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        check_profile_version=1,
        lifecycle_status="queued",
        test_outcome="pending",
        analysis_outcome="not_requested",
        correlation_id="corr-phase3-test",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    import sys

    check_definitions = [
        {
            "key": "unit_regression",
            "name": "Regression Test",
            "command": [sys.executable, "-m", "pytest", "-o", "rootdir=.", "test_regression.py"],
            "timeout_seconds": 60,
        }
    ]

    outcome = execute_run_checks(
        db=db,
        run=run,
        check_definitions=check_definitions,
        workspace_dir=str(fixture_dir),
    )

    # Authoritative deterministic test outcome
    assert outcome == TestOutcome.FAILED
    assert run.test_outcome == "failed"
    assert run.lifecycle_status == "failed"

    # Verify CheckRun record
    check_run = db.query(CheckRun).filter(CheckRun.run_id == run.id).first()
    assert check_run is not None
    assert check_run.status == "failed"
    assert check_run.exit_code != 0
    assert check_run.duration_ms > 0

    # Verify EvidenceItem persisted
    evidence = db.query(EvidenceItem).filter(EvidenceItem.run_id == run.id).first()
    assert evidence is not None
    assert "Regression detected" in evidence.content_redacted
    assert evidence.truncated is False
    assert evidence.byte_length > 0
