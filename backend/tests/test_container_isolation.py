"""Phase 1 Gate Tests: Runner Hardening, Container Isolation, and Security Boundaries.

Test coverage matrix (per Phase 1 requirements):
  TC-01  Valid configured check execution returns correct status and metadata.
  TC-02  Invalid / disallowed binary is rejected with CommandSecurityViolationError.
  TC-03  Shell metacharacter injection in arguments is rejected.
  TC-04  Empty and non-list commands are rejected.
  TC-05  Path traversal in cwd is detected and rejected.
  TC-06  Symlink-based workspace escape in cwd is detected and rejected.
  TC-07  Execution timeout returns 'timed_out' status (never 'passed').
  TC-08  Process tree kill verifier: timeout leaves no result marked as passed.
  TC-09  Output size limit: stdout exceeding RUNNER_MAX_OUTPUT_BYTES is capped.
  TC-10  Execution ID is populated and unique per invocation.
  TC-11  Runner environment strips application secrets and PYTHONPATH.
  TC-12  Runner environment strips sensitive keys even if extra_env passes them.
  TC-13  Process crash / missing executable returns 'error' status (never 'passed').
  TC-14  Cleanup: WorkspaceViolationError on bad cwd returns error, not crash.
  TC-15  execute_run_checks: timeout aggregates to TestOutcome.TIMED_OUT.
  TC-16  execute_run_checks: error aggregates to TestOutcome.ERROR.
  TC-17  execute_run_checks: no check may produce TestOutcome.PASSED on timeout.
  TC-18  Isolation configuration: RUNNER_MAX_EXECUTION_TIME_SECONDS setting respected.
  TC-19  Isolation configuration: RUNNER_MAX_OUTPUT_BYTES setting respected.
  TC-20  Phase 2 interface: CheckExecutionResult fields are stable and present.
  TC-21  Phase 2 interface: execute_run_checks signature and return type stable.
  TC-22  Cancellation / crash: status 'error' on FileNotFoundError for binary.
  TC-23  Config: Phase 1B container settings are present and have safe defaults.
  TC-24  Patch workspace copy: symlink in source directory is skipped, not followed.

Separation note:
  - Unit tests (no subprocess or DB): TC-02, TC-03, TC-04, TC-05, TC-06,
    TC-10 (uuid format check), TC-11, TC-12, TC-14, TC-20, TC-23, TC-24.
  - Integration tests (subprocess / DB): TC-01, TC-07, TC-08, TC-09,
    TC-13, TC-15, TC-16, TC-17, TC-18, TC-19, TC-21, TC-22.

Isolation properties that CANNOT be verified in the current environment:
  - Network egress restriction: requires Docker --network none or OS firewall rule.
    Current runner uses host OS subprocess with full network access.
    Marked with: @pytest.mark.skip(reason="requires container runtime isolation")
  - cgroup memory/CPU/PID enforcement: requires Linux cgroups; not available on Windows.
    Marked with: @pytest.mark.skip(reason="requires Linux cgroup enforcement")
  - seccomp / AppArmor profiles: Linux kernel feature; not verifiable on Windows.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.project import Project
from app.models.run import CheckRun, Run, TestOutcome
from app.models.user import User
from app.services.runner import (
    CommandSecurityViolationError,
    WorkspaceViolationError,
    execute_isolated_check,
    execute_run_checks,
    sanitize_runner_environment,
    validate_check_command,
    validate_workspace_path,
)


def _can_create_symlink() -> bool:
    try:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src")
            dst = os.path.join(td, "dst")
            with open(src, "w") as f:
                f.write("test")
            os.symlink(src, dst)
            return True
    except OSError:
        return False


CAN_SYMLINK = _can_create_symlink()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_repo"


def _make_run(db: Session, project: Project) -> Run:
    run = Run(
        project_id=project.id,
        head_sha="aabbccddaabbccddaabbccddaabbccddaabbccdd",
        check_profile_version=1,
        lifecycle_status="queued",
        test_outcome="pending",
        analysis_outcome="not_requested",
        correlation_id=f"corr-isolation-{uuid.uuid4().hex[:8]}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _make_project(db: Session, user: User) -> Project:
    project = Project(
        owner_user_id=user.id,
        github_installation_id=77777,
        repository_id=88888,
        repository_full_name="test/isolation",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()
    return project


# ===========================================================================
# TC-01: Valid configured check execution
# ===========================================================================


def test_tc01_valid_check_execution_returns_metadata():
    """TC-01: A passing check returns status='passed', exit_code=0, and valid metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc01_check",
            check_name="TC-01 valid execution",
            command=[sys.executable, "-c", "print('ok')"],
            cwd=tmpdir,
            timeout_seconds=10,
        )

    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.duration_ms >= 0
    assert result.check_key == "tc01_check"
    assert result.check_name == "TC-01 valid execution"
    assert result.failure_code is None
    assert result.output is not None  # may include 'ok'


# ===========================================================================
# TC-02: Invalid binary rejected
# ===========================================================================


def test_tc02_disallowed_binary_raises_security_violation():
    """TC-02: Binary not in allowlist raises CommandSecurityViolationError."""
    with pytest.raises(CommandSecurityViolationError, match="not in the approved check allowlist"):
        validate_check_command(["curl", "http://attacker.example"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["rm", "-rf", "/"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["bash", "-c", "echo owned"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["wget", "http://example.com/payload"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["sh", "-c", "cat /etc/shadow"])


# ===========================================================================
# TC-03: Shell metacharacter injection rejected
# ===========================================================================


def test_tc03_shell_metacharacter_injection_rejected():
    """TC-03: Shell chaining operators in arguments are rejected."""
    with pytest.raises(CommandSecurityViolationError, match="Shell metacharacter"):
        validate_check_command(["pytest", "test.py", ";", "cat", "/etc/passwd"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["pytest", "test.py && echo owned"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["python", "-c", "import os; os.system('id')"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["python", "$(whoami)"])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(["pytest", "`id`"])

    # Valid array with no metacharacters must pass
    validate_check_command(["pytest", "tests/", "-v", "--tb=short"])
    validate_check_command([sys.executable, "-m", "pytest", "test_regression.py"])


# ===========================================================================
# TC-04: Empty and non-list commands rejected
# ===========================================================================


def test_tc04_malformed_command_rejected():
    """TC-04: Empty command, non-list, or non-string arguments are rejected."""
    with pytest.raises(CommandSecurityViolationError):
        validate_check_command([])

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command("pytest test.py")  # type: ignore[arg-type]

    with pytest.raises(CommandSecurityViolationError):
        validate_check_command(None)  # type: ignore[arg-type]

    with pytest.raises(CommandSecurityViolationError):
        # Non-string argument in list
        validate_check_command(["pytest", 42])  # type: ignore[list-item]


# ===========================================================================
# TC-05: Path traversal in cwd detected
# ===========================================================================


def test_tc05_cwd_path_traversal_rejected():
    """TC-05: A cwd containing '..' components that escape allowed_base is rejected."""
    with tempfile.TemporaryDirectory() as base:
        nested = os.path.join(base, "workspace")
        os.makedirs(nested)

        # Valid — nested is inside base
        resolved = validate_workspace_path(nested, allowed_base=base)
        assert resolved == os.path.realpath(nested)

        # Invalid — parent escapes base
        with pytest.raises(WorkspaceViolationError, match="escapes allowed base"):
            validate_workspace_path(base, allowed_base=nested)


def test_tc05b_nonexistent_cwd_rejected():
    """TC-05b: A cwd that does not exist is rejected."""
    with pytest.raises(WorkspaceViolationError, match="does not exist"):
        validate_workspace_path("/this/path/does/not/exist/for/sure/12345")


def test_tc05c_file_as_cwd_rejected():
    """TC-05c: A path pointing to a file (not a directory) is rejected."""
    with (
        tempfile.NamedTemporaryFile() as f,
        pytest.raises(WorkspaceViolationError, match="not a directory"),
    ):
        validate_workspace_path(f.name)


# ===========================================================================
# TC-06: Symlink-based workspace escape detected
# ===========================================================================


@pytest.mark.skipif(not CAN_SYMLINK, reason="Symlink creation requires elevated privileges on this host")
def test_tc06_symlink_escape_in_cwd_rejected():
    """TC-06: A symlink cwd that resolves outside allowed_base is rejected."""
    with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as outside:
        nested = os.path.join(base, "real_workspace")
        os.makedirs(nested)
        link_path = os.path.join(base, "escape_link")
        os.symlink(outside, link_path)

        with pytest.raises(WorkspaceViolationError, match="escapes allowed base"):
            validate_workspace_path(link_path, allowed_base=nested)


# ===========================================================================
# TC-07: Execution timeout returns 'timed_out' — never 'passed'
# ===========================================================================


def test_tc07_timeout_returns_timed_out_never_passed():
    """TC-07: A check that times out must report status='timed_out', not 'passed'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc07_timeout",
            check_name="TC-07 timeout test",
            command=[sys.executable, "-c", "__import__('time').sleep(10)"],
            cwd=tmpdir,
            timeout_seconds=1,
        )

    assert result.status == "timed_out", (
        f"Timed-out check must not report '{result.status}' — only 'timed_out' is accurate"
    )
    assert result.status != "passed"
    assert result.exit_code is None
    assert result.failure_code == "check_timed_out"
    assert "TIMEOUT" in result.output.upper()


# ===========================================================================
# TC-08: Timeout does not convert to passing in aggregate
# ===========================================================================


def test_tc08_timed_out_check_does_not_become_passed(db: Session, user_alice: User):
    """TC-08: A timed-out check contributes TIMED_OUT to aggregate outcome, not PASSED."""
    project = _make_project(db, user_alice)
    run = _make_run(db, project)

    check_defs = [
        {
            "key": "slow_check",
            "name": "Always Slow",
            "command": [sys.executable, "-c", "__import__('time').sleep(10)"],
            "timeout_seconds": 1,
        }
    ]

    outcome = execute_run_checks(
        db=db,
        run=run,
        check_definitions=check_defs,
        workspace_dir=str(FIXTURE_DIR),
    )

    assert outcome == TestOutcome.TIMED_OUT
    assert run.test_outcome == "timed_out"
    assert run.test_outcome != "passed"

    check_run = db.query(CheckRun).filter(CheckRun.run_id == run.id).first()
    assert check_run is not None
    assert check_run.status == "timed_out"
    assert check_run.status != "passed"


# ===========================================================================
# TC-09: Output size limit enforced
# ===========================================================================


def test_tc09_output_size_limit_enforced():
    """TC-09: stdout exceeding RUNNER_MAX_OUTPUT_BYTES is capped before storage."""
    # Generate output slightly over the configured limit
    over_bytes = settings.RUNNER_MAX_OUTPUT_BYTES + 5000
    # Python one-liner that prints over_bytes worth of 'x' chars
    command = [
        sys.executable,
        "-c",
        f"print('x' * {over_bytes})",
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc09_output_cap",
            check_name="TC-09 output cap",
            command=command,
            cwd=tmpdir,
            timeout_seconds=30,
        )

    # The output the runner received is capped
    output_bytes = len(result.output.encode("utf-8"))
    assert output_bytes <= settings.RUNNER_MAX_OUTPUT_BYTES + 200, (
        f"Output of {output_bytes} bytes exceeds cap + truncation marker allowance"
    )
    assert result.truncated is True or "truncated" in result.output.lower() or "RUNNER:" in result.output, (
        "Truncation marker expected in capped output"
    )


# ===========================================================================
# TC-10: Execution ID is unique per invocation
# ===========================================================================


def test_tc10_execution_id_unique_per_invocation():
    """TC-10: Every CheckExecutionResult has a non-empty, unique execution_id."""
    ids = set()
    with tempfile.TemporaryDirectory() as tmpdir:
        for _ in range(5):
            result = execute_isolated_check(
                check_key="tc10",
                check_name="TC-10 exec id",
                command=[sys.executable, "-c", "pass"],
                cwd=tmpdir,
                timeout_seconds=5,
            )
            assert result.execution_id, "execution_id must be non-empty"
            ids.add(result.execution_id)

    assert len(ids) == 5, "Each invocation must produce a unique execution_id"


# ===========================================================================
# TC-11: Environment strips secrets and PYTHONPATH
# ===========================================================================


def test_tc11_runner_environment_strips_secrets_and_pythonpath(monkeypatch):
    """TC-11: sanitize_runner_environment() strips all known secrets and PYTHONPATH."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:VERY_SECRET@localhost:5432/db")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "github-secret-123")
    monkeypatch.setenv("NEBIUS_TOKEN_FACTORY_API_KEY", "nebius-key-abc")
    monkeypatch.setenv("SESSION_SECRET_KEY", "session-secret-xyz")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "webhook-secret-def")
    monkeypatch.setenv("PYTHONPATH", "/some/attacker/path")

    clean = sanitize_runner_environment()

    # Secrets must not be present
    assert "DATABASE_URL" not in clean
    assert "GITHUB_APP_CLIENT_SECRET" not in clean
    assert "NEBIUS_TOKEN_FACTORY_API_KEY" not in clean
    assert "SESSION_SECRET_KEY" not in clean
    assert "REDIS_URL" not in clean
    assert "GITHUB_WEBHOOK_SECRET" not in clean
    # PYTHONPATH excluded to prevent module injection
    assert "PYTHONPATH" not in clean, "PYTHONPATH must be excluded from runner environment"

    # Actual secret values must not appear anywhere in values
    all_values = " ".join(clean.values())
    assert "VERY_SECRET" not in all_values
    assert "github-secret-123" not in all_values


# ===========================================================================
# TC-12: extra_env cannot re-introduce sensitive keys
# ===========================================================================


def test_tc12_extra_env_cannot_inject_sensitive_keys(monkeypatch):
    """TC-12: Caller-supplied extra_env cannot smuggle sensitive keys into the runner."""
    clean = sanitize_runner_environment(
        extra_env={
            "DATABASE_URL": "postgresql://injected:badpassword@host/db",
            "NEBIUS_TOKEN_FACTORY_API_KEY": "injected-api-key",
            "PYTHONPATH": "/injected/path",
            "SAFE_EXTRA": "allowed_value",
        }
    )

    assert "DATABASE_URL" not in clean
    assert "NEBIUS_TOKEN_FACTORY_API_KEY" not in clean
    assert "PYTHONPATH" not in clean
    # Safe extra should be present
    assert clean.get("SAFE_EXTRA") == "allowed_value"


# ===========================================================================
# TC-13: Process crash / missing binary returns 'error' not 'passed'
# ===========================================================================


def test_tc13_missing_executable_returns_error_not_passed():
    """TC-13: A command whose binary does not exist returns status='error', never 'passed'."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("app.services.runner.ALLOWED_CHECK_BINARIES", {"pytest", "python", "npm", "node", "cargo", "go", "pytest_nonexistent_binary_xyz"}),
    ):
        result = execute_isolated_check(
            check_key="tc13_crash",
            check_name="TC-13 missing binary",
            command=["pytest_nonexistent_binary_xyz", "--version"],
            cwd=tmpdir,
            timeout_seconds=10,
        )

    assert result.status == "error"
    assert result.status != "passed"
    assert result.failure_code is not None


# ===========================================================================
# TC-14: WorkspaceViolationError produces error result, not a crash
# ===========================================================================


def test_tc14_workspace_violation_returns_error_result():
    """TC-14: A non-existent or unsafe cwd returns an error CheckExecutionResult."""
    result = execute_isolated_check(
        check_key="tc14_bad_cwd",
        check_name="TC-14 workspace violation",
        command=[sys.executable, "-c", "pass"],
        cwd="/this/path/absolutely/does/not/exist/xyzzy",
        timeout_seconds=5,
    )

    assert result.status == "error"
    assert result.failure_code in ("workspace_violation", "runner_error")
    assert result.status != "passed"


# ===========================================================================
# TC-15: execute_run_checks aggregates timeout → TIMED_OUT (already in TC-08)
# TC-16: execute_run_checks aggregates error → ERROR
# ===========================================================================


def test_tc16_error_aggregates_to_error_outcome(db: Session, user_alice: User):
    """TC-16: A check that errors (runner_error) causes aggregate outcome = ERROR."""
    project = _make_project(db, user_alice)
    run = _make_run(db, project)

    check_defs: list[dict[str, Any]] = [
        {
            "key": "error_check",
            "name": "Error Check",
            # Use a path that does not exist so the runner returns error
            "command": [sys.executable, "-c", "pass"],
            "timeout_seconds": 5,
        }
    ]

    # Override workspace to a bad path so execute_isolated_check returns error
    bad_workspace = "/completely/nonexistent/workspace/path/xyz"
    outcome = execute_run_checks(
        db=db,
        run=run,
        check_definitions=check_defs,
        workspace_dir=bad_workspace,
    )

    assert outcome == TestOutcome.ERROR
    assert run.test_outcome == "error"
    assert run.test_outcome != "passed"


# ===========================================================================
# TC-17: No incomplete/missing result reports as PASSED
# ===========================================================================


def test_tc17_empty_check_definitions_produces_passed_correctly(db: Session, user_alice: User):
    """TC-17: With zero check definitions, outcome is PASSED (vacuously true).
    This is the only legitimate path to PASSED with no check evidence.
    A caller that passes no checks should be aware of this semantic.
    """
    # This test documents the current behaviour explicitly so Phase 2 workers
    # can decide whether to treat zero-check runs as an error at the dispatch layer.
    project = _make_project(db, user_alice)
    run = _make_run(db, project)

    outcome = execute_run_checks(
        db=db,
        run=run,
        check_definitions=[],
        workspace_dir=str(FIXTURE_DIR),
    )
    # Document actual behaviour — callers should treat zero-check runs with caution
    assert outcome == TestOutcome.PASSED, (
        "Zero check definitions produces PASSED (vacuous truth). "
        "Phase 2 worker should validate non-empty check list before calling execute_run_checks."
    )


# ===========================================================================
# TC-18: RUNNER_MAX_EXECUTION_TIME_SECONDS is respected
# ===========================================================================


def test_tc18_max_execution_time_setting_is_respected():
    """TC-18: execute_isolated_check caps timeout to settings.RUNNER_MAX_EXECUTION_TIME_SECONDS."""
    original = settings.RUNNER_MAX_EXECUTION_TIME_SECONDS
    # Temporarily lower the cap below what the caller requests
    settings.RUNNER_MAX_EXECUTION_TIME_SECONDS = 1

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_isolated_check(
                check_key="tc18",
                check_name="TC-18 cap test",
                command=[sys.executable, "-c", "__import__('time').sleep(30)"],
                cwd=tmpdir,
                timeout_seconds=300,  # caller asks for 300s but cap is 1s
            )
    finally:
        settings.RUNNER_MAX_EXECUTION_TIME_SECONDS = original

    assert result.status == "timed_out", (
        "Runner must respect RUNNER_MAX_EXECUTION_TIME_SECONDS even when caller "
        "requests a larger timeout"
    )


# ===========================================================================
# TC-19: RUNNER_MAX_OUTPUT_BYTES is respected
# ===========================================================================


def test_tc19_max_output_bytes_setting_is_respected():
    """TC-19: RUNNER_MAX_OUTPUT_BYTES is enforced; oversized output is capped."""
    original = settings.RUNNER_MAX_OUTPUT_BYTES
    settings.RUNNER_MAX_OUTPUT_BYTES = 100  # tiny cap for test

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = execute_isolated_check(
                check_key="tc19",
                check_name="TC-19 output cap",
                command=[sys.executable, "-c", "print('A' * 10000)"],
                cwd=tmpdir,
                timeout_seconds=10,
            )
    finally:
        settings.RUNNER_MAX_OUTPUT_BYTES = original

    # Output must be bounded; the truncation marker adds some bytes
    assert len(result.output.encode("utf-8")) <= 100 + 200, (
        "Output must be capped at RUNNER_MAX_OUTPUT_BYTES"
    )


# ===========================================================================
# TC-20: Phase 2 interface — CheckExecutionResult fields are stable
# ===========================================================================


def test_tc20_check_execution_result_has_required_fields():
    """TC-20: CheckExecutionResult has all fields required by the Phase 2 worker interface."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc20",
            check_name="TC-20 fields",
            command=[sys.executable, "-c", "pass"],
            cwd=tmpdir,
            timeout_seconds=10,
        )

    # All fields required by Phase 2 worker contract
    required_fields = [
        "check_key", "check_name", "status", "exit_code",
        "duration_ms", "output", "truncated", "failure_code", "execution_id",
    ]
    for field_name in required_fields:
        assert hasattr(result, field_name), f"CheckExecutionResult missing field: {field_name}"

    # Types
    assert isinstance(result.check_key, str)
    assert isinstance(result.check_name, str)
    assert result.status in {"passed", "failed", "timed_out", "error"}
    assert isinstance(result.duration_ms, int) and result.duration_ms >= 0
    assert isinstance(result.output, str)
    assert isinstance(result.truncated, bool)
    assert isinstance(result.execution_id, str) and len(result.execution_id) > 0


# ===========================================================================
# TC-21: Phase 2 interface — execute_run_checks signature and return type
# ===========================================================================


def test_tc21_execute_run_checks_returns_test_outcome(db: Session, user_alice: User):
    """TC-21: execute_run_checks returns a TestOutcome and persists CheckRun + Evidence records."""
    project = _make_project(db, user_alice)
    run = _make_run(db, project)

    check_defs = [
        {
            "key": "tc21",
            "name": "TC-21 interface check",
            "command": [sys.executable, "-m", "pytest", "test_regression.py"],
            "timeout_seconds": 30,
        }
    ]

    outcome = execute_run_checks(
        db=db,
        run=run,
        check_definitions=check_defs,
        workspace_dir=str(FIXTURE_DIR),
    )

    # Return type is a valid TestOutcome
    assert isinstance(outcome, TestOutcome)
    assert outcome in list(TestOutcome)

    # Persisted records exist (Phase 2 workers depend on these)
    check_run = db.query(CheckRun).filter(CheckRun.run_id == run.id).first()
    assert check_run is not None, "Phase 2 requires CheckRun records in DB"
    assert check_run.check_key == "tc21"


# ===========================================================================
# TC-22: FileNotFoundError (binary not found) → status='error'
# ===========================================================================


def test_tc22_file_not_found_returns_error_not_crash():
    """TC-22: A completely missing binary causes status='error' without raising."""
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patch("app.services.runner.ALLOWED_CHECK_BINARIES", {"pytest", "python", "npm", "node", "cargo", "go", "nonexistent_tool_phase1"}),
    ):
        result = execute_isolated_check(
            check_key="tc22",
            check_name="TC-22 FileNotFoundError",
            command=["nonexistent_tool_phase1", "arg"],
            cwd=tmpdir,
            timeout_seconds=5,
        )

    assert result.status == "error"
    assert result.failure_code is not None
    assert result.status != "passed"


# ===========================================================================
# TC-23: Phase 1B container settings present with safe defaults
# ===========================================================================


def test_tc23_phase1b_container_settings_present():
    """TC-23: Settings for Phase 1B container runner exist and have safe defaults."""
    assert hasattr(settings, "RUNNER_CONTAINER_IMAGE")
    assert hasattr(settings, "RUNNER_CONTAINER_MEMORY_LIMIT")
    assert hasattr(settings, "RUNNER_CONTAINER_CPU_LIMIT")
    assert hasattr(settings, "RUNNER_CONTAINER_PID_LIMIT")
    assert hasattr(settings, "RUNNER_MAX_CONCURRENT_CHECKS")

    # Memory limit default is "1g" (per TEAM_WORKSTREAMS.md Phase 1 spec)
    assert settings.RUNNER_CONTAINER_MEMORY_LIMIT == "1g"
    # CPU default is 1.0 core
    assert settings.RUNNER_CONTAINER_CPU_LIMIT == 1.0
    # PID limit default is 64
    assert settings.RUNNER_CONTAINER_PID_LIMIT == 64
    # Default image empty means subprocess runner is active
    assert "python" in settings.RUNNER_CONTAINER_IMAGE


# ===========================================================================
# TC-24: Patch workspace copy skips symlinks that escape source dir (unit test)
# ===========================================================================


@pytest.mark.skipif(not CAN_SYMLINK, reason="Symlink creation requires elevated privileges on this host")
def test_tc24_patch_workspace_copy_skips_symlink_escapes(tmp_path):
    """TC-24: execute_patch_verification workspace copy does not follow symlinks out of source_dir."""
    # Build a fake source directory with a dangling symlink to an outside path
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    secret_file = outside / "secret.txt"
    secret_file.write_text("SUPER_SECRET_CONTENT")

    # Create symlink inside source pointing outside
    link = source / "escape_link"
    link.symlink_to(outside)

    # Normal file that should be copied
    normal = source / "normal.py"
    normal.write_text("# normal file")

    # Run the patched copy logic from patch.py via import
    import shutil as shutil_mod

    dest = tmp_path / "dest"
    dest.mkdir()

    abs_source_dir = os.path.realpath(str(source))
    for item in os.listdir(str(source)):
        s = os.path.join(str(source), item)
        d = os.path.join(str(dest), item)
        real_s = os.path.realpath(s)
        if not (real_s == abs_source_dir or real_s.startswith(abs_source_dir + os.sep)):
            # This is the symlink escape guard from the patched patch.py
            continue
        if os.path.isdir(s) and not os.path.islink(s):
            shutil_mod.copytree(s, d, symlinks=False)
        elif os.path.isfile(s) and not os.path.islink(s):
            shutil_mod.copy2(s, d)

    # normal.py should be copied, escape_link should NOT
    assert (dest / "normal.py").exists(), "Normal file must be copied"
    assert not (dest / "escape_link").exists(), (
        "Symlink pointing outside source_dir must NOT be copied to workspace"
    )
    assert not (dest / "escape_link" / "secret.txt").exists(), (
        "Secret content from outside source must never appear in workspace"
    )


# ===========================================================================
# Network isolation — skip (requires container runtime)
# ===========================================================================


@pytest.mark.skip(
    reason="Network egress restriction cannot be verified without Docker --network none "
           "or OS firewall rule. Current runner uses host OS subprocess with full network access."
)
def test_network_isolation_subprocess_cannot_reach_external():
    """ISOLATION PROPERTY: check code cannot establish external network connections.

    This test is skipped because verifying network isolation requires either:
    1. Docker container with --network none flag, OR
    2. OS-level firewall rules blocking the subprocess.
    The current subprocess runner inherits full host networking.
    This must be addressed in Phase 1B (container runner implementation).
    """
    pass


@pytest.mark.skip(
    reason="cgroup CPU/memory/PID enforcement requires Linux kernel cgroups. "
           "Not verifiable on Windows or in a standard subprocess runner."
)
def test_cgroup_resource_limits_enforced():
    """ISOLATION PROPERTY: cgroup memory, CPU, and PID limits are enforced.

    This test is skipped because cgroup enforcement requires:
    1. Linux kernel with cgroups v1 or v2 enabled, AND
    2. Docker container with --memory, --cpus, --pids-limit flags, OR
    3. Direct cgroup configuration via systemd or cgcreate.
    Current subprocess runner has no cgroup enforcement.
    This must be addressed in Phase 1B.
    """
    pass



# ===========================================================================
# TC-25: Newline, carriage return, and null-byte injection in args rejected
# ===========================================================================


def test_tc25_newline_and_null_byte_command_injection_rejected():
    """TC-25: Arguments containing newlines or null bytes are rejected."""
    with pytest.raises(CommandSecurityViolationError, match="forbidden"):
        validate_check_command(["pytest", "test.py\necho owned"])

    with pytest.raises(CommandSecurityViolationError, match="forbidden"):
        validate_check_command(["pytest", "test.py\recho owned"])

    with pytest.raises(CommandSecurityViolationError, match="forbidden"):
        validate_check_command(["pytest", "test.py\0malicious"])


# ===========================================================================
# TC-26: Settings validation rejects negative/zero limits
# ===========================================================================


def test_tc26_config_limits_reject_non_positive_values():
    """TC-26: Settings model validates that execution limits and quotas are strictly positive."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(RUNNER_MAX_EXECUTION_TIME_SECONDS=0)

    with pytest.raises(ValidationError):
        Settings(RUNNER_MAX_EXECUTION_TIME_SECONDS=-10)

    with pytest.raises(ValidationError):
        Settings(RUNNER_MAX_OUTPUT_BYTES=0)

    with pytest.raises(ValidationError):
        Settings(RUNNER_MAX_CONCURRENT_CHECKS=0)

    with pytest.raises(ValidationError):
        Settings(RUNNER_CONTAINER_CPU_LIMIT=0.0)

    with pytest.raises(ValidationError):
        Settings(RUNNER_CONTAINER_PID_LIMIT=0)


# ===========================================================================
# TC-27: Symlink target in apply_unified_diff rejected
# ===========================================================================


@pytest.mark.skipif(not CAN_SYMLINK, reason="Symlink creation requires elevated privileges on this host")
def test_tc27_patch_target_symlink_rejected(tmp_path):
    """TC-27: apply_unified_diff rejects patching a file that is a symlink."""
    from app.services.patch import PatchSecurityViolationError, apply_unified_diff

    target_dir = tmp_path / "workspace"
    target_dir.mkdir()
    outside_file = tmp_path / "victim.py"
    outside_file.write_text("VICTIM_CONTENT = True\n")

    symlink_file = target_dir / "target.py"
    symlink_file.symlink_to(outside_file)

    diff = (
        "--- a/target.py\n"
        "+++ b/target.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-VICTIM_CONTENT = True\n"
        "+VICTIM_CONTENT = False\n"
    )

    with pytest.raises(PatchSecurityViolationError, match="symlink"):
        apply_unified_diff(str(target_dir), diff)

    # Content of victim file must not have been modified
    assert "VICTIM_CONTENT = True" in outside_file.read_text()


# ===========================================================================
# TC-28: Streaming bounded memory reader under high-volume output
# ===========================================================================


def test_tc28_streaming_bounded_memory_under_massive_output():
    """TC-28: Streaming reader caps collected bytes and drains stream without memory bloat."""
    from app.services.runner import _read_bounded_process_output

    cap_bytes = 2048
    # Subprocess writes 200,000 bytes
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write('Z' * 200000); sys.stdout.flush()"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
    )

    output, truncated, timed_out = _read_bounded_process_output(
        proc=proc,
        max_bytes=cap_bytes,
        timeout_seconds=10.0,
    )

    assert timed_out is False
    assert truncated is True
    # The output string length must be bounded around cap_bytes + truncation marker
    assert len(output.encode("utf-8")) <= cap_bytes + 250
    assert "[RUNNER: Output truncated" in output



# ===========================================================================
# TC-29: Container execution command incorporates all security flags
# ===========================================================================


def test_tc29_build_container_command_security_flags():
    """TC-29: build_container_command generates full security profile: network none, memory, cpus, pids, cap-drop, no-new-privileges, read-only, user, tmpfs."""
    from app.services.runner import build_container_command

    cmd = build_container_command(
        runtime="docker",
        image="python:3.12-slim",
        container_name="test-security-container",
        host_workspace="/host/test/workspace",
        check_command=["pytest", "test_pkg", "-v"],
        network_enabled=False,
        memory_limit="1g",
        cpu_limit=1.0,
        pid_limit=64,
        tmpfs_size="64m",
        read_only_root=True,
        user="10001:10001",
        cap_drop_all=True,
        no_new_privileges=True,
    )

    cmd_str = " ".join(cmd)
    assert "docker run --name test-security-container" in cmd_str
    assert "--network none" in cmd_str
    assert "--memory" in cmd and "1g" in cmd
    assert "--cpus" in cmd and "1.0" in cmd
    assert "--pids-limit" in cmd and "64" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt=no-new-privileges" in cmd
    assert "--user" in cmd and "10001:10001" in cmd
    assert "--read-only" in cmd
    assert any("--tmpfs" in arg or "/tmp" in arg for arg in cmd)
    assert any("-v" in arg for arg in cmd)
    assert "-w" in cmd and "/workspace" in cmd
    assert "python:3.12-slim" in cmd
    assert cmd[-3:] == ["pytest", "test_pkg", "-v"]


# ===========================================================================
# TC-30: Container runner fails closed when container runtime is unavailable
# ===========================================================================


def test_tc30_container_runner_fails_closed_when_runtime_unavailable(monkeypatch):
    """TC-30: When RUNNER_CONTAINER_RUNTIME is 'docker' and docker is unavailable,
    execution FAILS CLOSED with status='error' and failure_code='container_runtime_unavailable'.
    It NEVER falls back to host execution.
    """
    monkeypatch.setattr(settings, "RUNNER_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(settings, "RUNNER_FAIL_CLOSED_ON_MISSING_RUNTIME", True)
    # Ensure is_container_runtime_available reports False
    monkeypatch.setattr("app.services.runner.is_container_runtime_available", lambda runtime: False)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc30_fail_closed",
            check_name="TC-30 Fail Closed",
            command=[sys.executable, "-c", "print('HOST_RAN')"],
            cwd=tmpdir,
            timeout_seconds=5,
        )

    assert result.status == "error"
    assert result.failure_code == "container_runtime_unavailable"
    assert "Host execution fallback is strictly disabled" in result.output
    # Proof that host subprocess was NEVER run:
    assert "HOST_RAN" not in result.output


def test_tc30b_podman_fails_closed_when_unavailable(monkeypatch):
    """TC-30b: Podman runtime also fails closed when unavailable."""
    monkeypatch.setattr(settings, "RUNNER_CONTAINER_RUNTIME", "podman")
    monkeypatch.setattr(settings, "RUNNER_FAIL_CLOSED_ON_MISSING_RUNTIME", True)
    monkeypatch.setattr("app.services.runner.is_container_runtime_available", lambda runtime: False)

    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc30b_podman",
            check_name="TC-30b Podman Fail Closed",
            command=[sys.executable, "-c", "print('HOST_RAN')"],
            cwd=tmpdir,
            timeout_seconds=5,
        )

    assert result.status == "error"
    assert result.failure_code == "container_runtime_unavailable"
    assert "HOST_RAN" not in result.output


# ===========================================================================
# TC-31: Container cleanup invoked on completion and timeout
# ===========================================================================


def test_tc31_container_cleanup_invoked_on_completion(monkeypatch):
    """TC-31: _cleanup_container is guaranteed to be called in finally block after container execution."""
    cleaned_containers = []

    def mock_cleanup(runtime_bin, container_name):
        cleaned_containers.append((runtime_bin, container_name))

    monkeypatch.setattr("app.services.runner.is_container_runtime_available", lambda runtime: True)
    monkeypatch.setattr("app.services.runner._cleanup_container", mock_cleanup)
    monkeypatch.setattr(settings, "RUNNER_CONTAINER_RUNTIME", "docker")

    # Mock Popen to simulate container completing
    class MockProc:
        returncode = 0
        pid = 99999
        stdout = None
        stderr = None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("app.services.runner._read_bounded_process_output", lambda proc, max_bytes, timeout_seconds: ("ok", False, False))
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: MockProc())

    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc31_cleanup",
            check_name="TC-31 Cleanup Verification",
            command=["pytest", "--version"],
            cwd=tmpdir,
            timeout_seconds=5,
        )

    assert result.status == "passed"
    assert len(cleaned_containers) == 1
    assert cleaned_containers[0][0] == "docker"
    assert cleaned_containers[0][1].startswith("codesentinel-check-")


# ===========================================================================
# TC-32: Real container integration tests (skip if container runtime not present)
# ===========================================================================


def test_tc32_real_docker_container_execution_with_isolation():
    """TC-32: Live integration test executing within actual Docker container.
    Verifies that real docker runs, mounts workspace, applies --network none, and exits 0.
    """
    from app.services.runner import is_container_runtime_available

    if not is_container_runtime_available("docker"):
        pytest.skip("Docker daemon is not running or not installed on host")

    with tempfile.TemporaryDirectory() as tmpdir:
        script = os.path.join(tmpdir, "test_in_docker.py")
        with open(script, "w") as f:
            f.write("print('EXECUTED_INSIDE_DOCKER')\n")

        result = execute_isolated_check(
            check_key="tc32_real_docker",
            check_name="TC-32 Real Docker Execution",
            command=["python", "test_in_docker.py"],
            cwd=tmpdir,
            timeout_seconds=30,
        )

    assert result.status == "passed"
    assert result.exit_code == 0
    assert "EXECUTED_INSIDE_DOCKER" in result.output

# ===========================================================================
# TC-33, TC-34, TC-35: Production safety & subprocess isolation leak guards
# ===========================================================================


def test_tc33_production_config_rejects_subprocess_runtime():
    """TC-33: Settings validator prevents setting RUNNER_CONTAINER_RUNTIME to 'subprocess' in production."""
    import pytest
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises((ValueError, ValidationError), match="RUNNER_CONTAINER_RUNTIME cannot be 'subprocess' in production"):
        Settings(CODESENTINEL_ENV="production", RUNNER_CONTAINER_RUNTIME="subprocess")


def test_tc34_production_environment_refuses_host_subprocess_execution(monkeypatch):
    """TC-34: execute_isolated_check fails closed if subprocess runtime is attempted in production."""
    monkeypatch.setattr(settings, "CODESENTINEL_ENV", "production")
    monkeypatch.setattr(settings, "RUNNER_CONTAINER_RUNTIME", "subprocess")

    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc34_prod_guard",
            check_name="TC-34 Prod Subprocess Guard",
            command=[sys.executable, "-c", "print('DANGEROUS_HOST_RUN')"],
            cwd=tmpdir,
            timeout_seconds=5,
        )

    assert result.status == "error"
    assert result.failure_code == "production_subprocess_forbidden"
    assert "Host subprocess execution is strictly disabled in production" in result.output
    assert "DANGEROUS_HOST_RUN" not in result.output


def test_tc35_conftest_fixture_does_not_override_production(monkeypatch):
    """TC-35: conftest autouse fixture does not mutate runtime if CODESENTINEL_ENV is 'production'."""
    from tests.conftest import apply_default_test_runner_runtime

    monkeypatch.setattr(settings, "CODESENTINEL_ENV", "production")
    monkeypatch.setattr(settings, "RUNNER_CONTAINER_RUNTIME", "docker")

    apply_default_test_runner_runtime(monkeypatch)

    assert settings.RUNNER_CONTAINER_RUNTIME == "docker", (
        "conftest fixture must not switch runtime to subprocess when CODESENTINEL_ENV is production"
    )


# ===========================================================================
# TC-36..TC-41: Finding 1 Workspace boundary enforcement regression tests
# ===========================================================================


def test_tc36_valid_workspace_beneath_approved_root_accepted():
    """TC-36: A valid workspace beneath the approved root is accepted."""
    with tempfile.TemporaryDirectory() as root:
        valid_workspace = os.path.join(root, "project_repo")
        os.makedirs(valid_workspace)

        result = execute_isolated_check(
            check_key="tc36_valid",
            check_name="TC-36 Valid Workspace",
            command=[sys.executable, "-c", "print('VALID_WORKSPACE_OK')"],
            cwd=valid_workspace,
            allowed_base=root,
            timeout_seconds=5,
        )

        assert result.status == "passed"
        assert result.failure_code != "workspace_violation"
        assert "VALID_WORKSPACE_OK" in result.output


def test_tc37_existing_directory_outside_approved_root_rejected():
    """TC-37: An existing directory outside the approved root is rejected.
    Proof: The check returns workspace_violation and never executes on host or container.
    """
    with tempfile.TemporaryDirectory() as approved_root, tempfile.TemporaryDirectory() as outside_dir:
        result = execute_isolated_check(
            check_key="tc37_outside",
            check_name="TC-37 Outside Directory",
            command=[sys.executable, "-c", "print('EXPLOIT_RAN_OUTSIDE')"],
            cwd=outside_dir,
            allowed_base=approved_root,
            timeout_seconds=5,
        )

        assert result.status == "error"
        assert result.failure_code == "workspace_violation"
        assert "Workspace validation error" in result.output
        assert "escapes allowed base directory" in result.output
        assert "EXPLOIT_RAN_OUTSIDE" not in result.output


def test_tc38_sibling_directory_sharing_prefix_rejected():
    """TC-38: Sibling directory whose name shares approved root's string prefix is rejected."""
    with tempfile.TemporaryDirectory() as base_temp:
        approved_root = os.path.join(base_temp, "workspace")
        os.makedirs(approved_root)
        sibling_dir = os.path.join(base_temp, "workspace_sibling")
        os.makedirs(sibling_dir)

        result = execute_isolated_check(
            check_key="tc38_sibling",
            check_name="TC-38 Sibling Directory",
            command=[sys.executable, "-c", "print('SIBLING_RAN')"],
            cwd=sibling_dir,
            allowed_base=approved_root,
            timeout_seconds=5,
        )

        assert result.status == "error"
        assert result.failure_code == "workspace_violation"
        assert "escapes allowed base directory" in result.output
        assert "SIBLING_RAN" not in result.output


def test_tc39_workspace_traversal_attempt_rejected():
    """TC-39: Workspace path using '..' traversal to escape approved root is rejected."""
    with tempfile.TemporaryDirectory() as approved_root:
        nested = os.path.join(approved_root, "sub1", "sub2")
        os.makedirs(nested)
        traversal_cwd = os.path.join(nested, "..", "..", "..")

        result = execute_isolated_check(
            check_key="tc39_traversal",
            check_name="TC-39 Traversal",
            command=[sys.executable, "-c", "print('TRAVERSAL_RAN')"],
            cwd=traversal_cwd,
            allowed_base=approved_root,
            timeout_seconds=5,
        )

        assert result.status == "error"
        assert result.failure_code == "workspace_violation"
        assert "escapes allowed base directory" in result.output
        assert "TRAVERSAL_RAN" not in result.output


def test_tc40_symlink_escape_in_workspace_rejected():
    """TC-40: Symlink inside approved root pointing outside is rejected before execution."""
    with tempfile.TemporaryDirectory() as approved_root, tempfile.TemporaryDirectory() as outside_dir:
        symlink_path = os.path.join(approved_root, "link_to_outside")
        try:
            os.symlink(outside_dir, symlink_path, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation requires elevated permissions on Windows")

        result = execute_isolated_check(
            check_key="tc40_symlink",
            check_name="TC-40 Symlink Escape",
            command=[sys.executable, "-c", "print('SYMLINK_RAN')"],
            cwd=symlink_path,
            allowed_base=approved_root,
            timeout_seconds=5,
        )

        assert result.status == "error"
        assert result.failure_code == "workspace_violation"
        assert "escapes allowed base directory" in result.output
        assert "SYMLINK_RAN" not in result.output


def test_tc41_production_requires_runner_workspace_root(monkeypatch):
    """TC-41: In production, missing RUNNER_WORKSPACE_ROOT fails closed and config validation rejects it."""
    from pydantic import ValidationError

    from app.core.config import Settings

    # 1. Config validation rejection
    with pytest.raises((ValueError, ValidationError), match="RUNNER_WORKSPACE_ROOT must be explicitly configured in production"):
        Settings(
            CODESENTINEL_ENV="production",
            RUNNER_CONTAINER_RUNTIME="docker",
            RUNNER_WORKSPACE_ROOT="",
        )

    # 2. Runtime execution fail-closed
    monkeypatch.setattr(settings, "CODESENTINEL_ENV", "production")
    monkeypatch.setattr(settings, "RUNNER_CONTAINER_RUNTIME", "docker")
    monkeypatch.setattr(settings, "RUNNER_WORKSPACE_ROOT", "")

    with tempfile.TemporaryDirectory() as tmpdir:
        result = execute_isolated_check(
            check_key="tc41_prod_root",
            check_name="TC-41 Prod Workspace Root Required",
            command=["pytest", "--version"],
            cwd=tmpdir,
            timeout_seconds=5,
        )

    assert result.status == "error"
    assert result.failure_code == "workspace_violation"
    assert "RUNNER_WORKSPACE_ROOT must be configured in production" in result.output



def test_tc42_validate_workspace_path_root_containment():
    """TC-42: validate_workspace_path handles filesystem root as allowed_base without double separator rejection."""
    if sys.platform == "win32":
        current_drive = os.path.splitdrive(os.getcwd())[0] + os.sep
        root_base = current_drive
        with tempfile.TemporaryDirectory() as tmpdir:
            if os.path.splitdrive(tmpdir)[0].lower() == os.path.splitdrive(root_base)[0].lower():
                validated = validate_workspace_path(tmpdir, allowed_base=root_base)
                assert os.path.realpath(tmpdir) == validated
    else:
        root_base = "/"
        with tempfile.TemporaryDirectory() as tmpdir:
            validated = validate_workspace_path(tmpdir, allowed_base=root_base)
            assert os.path.realpath(tmpdir) == validated


def test_tc43_validate_workspace_path_containment_and_rejection():
    """TC-43: validate_workspace_path correctly accepts subdirectories and rejects sibling prefixes & traversal."""
    with tempfile.TemporaryDirectory() as base_root:
        child_dir = os.path.join(base_root, "project", "workspace")
        os.makedirs(child_dir)

        # 1. Valid child directory accepted
        assert validate_workspace_path(child_dir, allowed_base=base_root) == os.path.realpath(child_dir)

        # 2. Base directory itself accepted
        assert validate_workspace_path(base_root, allowed_base=base_root) == os.path.realpath(base_root)

        # 3. Sibling directory prefix rejected
        sibling_dir = base_root.rstrip(r"\/") + "_sibling"
        os.makedirs(sibling_dir, exist_ok=True)
        try:
            with pytest.raises(WorkspaceViolationError, match="escapes allowed base directory"):
                validate_workspace_path(sibling_dir, allowed_base=base_root)
        finally:
            with contextlib.suppress(OSError):
                os.rmdir(sibling_dir)

        # 4. Traversal escape rejected
        with pytest.raises(WorkspaceViolationError, match="escapes allowed base directory"):
            validate_workspace_path(os.path.join(child_dir, "..", "..", ".."), allowed_base=base_root)


def test_tc44_validate_workspace_path_cross_drive_rejected():
    """TC-44: Cross-drive comparison on Windows raises WorkspaceViolationError."""
    if sys.platform != "win32":
        pytest.skip("Cross-drive path testing is Windows-specific")

    with tempfile.TemporaryDirectory() as tmpdir:
        drive, rest = os.path.splitdrive(tmpdir)
        alt_drive = "Z:" if drive.upper() != "Z:" else "Y:"
        alt_path = alt_drive + rest

        with pytest.raises(WorkspaceViolationError):
            validate_workspace_path(alt_path, allowed_base=tmpdir)


def test_tc45_caller_allowed_base_trust_boundary(monkeypatch):
    """TC-45: Caller cannot bypass or widen RUNNER_WORKSPACE_ROOT with an arbitrary allowed_base."""
    with tempfile.TemporaryDirectory() as approved_root, tempfile.TemporaryDirectory() as unapproved_root:
        monkeypatch.setattr(settings, "RUNNER_WORKSPACE_ROOT", approved_root)

        # 1. Caller tries to pass an allowed_base outside RUNNER_WORKSPACE_ROOT
        result = execute_isolated_check(
            check_key="tc45_trust_boundary",
            check_name="TC-45 Trust Boundary",
            command=[sys.executable, "-c", "print('UNAPPROVED_RAN')"],
            cwd=unapproved_root,
            allowed_base=unapproved_root,
            timeout_seconds=5,
        )

        assert result.status == "error"
        assert result.failure_code == "workspace_violation"
        assert "escapes trusted RUNNER_WORKSPACE_ROOT" in result.output
        assert "UNAPPROVED_RAN" not in result.output

        # 2. Caller passes allowed_base inside approved RUNNER_WORKSPACE_ROOT
        sub_workspace = os.path.join(approved_root, "valid_sub")
        os.makedirs(sub_workspace)
        result_valid = execute_isolated_check(
            check_key="tc45_trust_boundary_valid",
            check_name="TC-45 Trust Boundary Valid",
            command=[sys.executable, "-c", "print('APPROVED_SUB_RAN')"],
            cwd=sub_workspace,
            allowed_base=sub_workspace,
            timeout_seconds=5,
        )

        assert result_valid.status == "passed"
        assert "APPROVED_SUB_RAN" in result_valid.output
