"""Execution supervisor for isolated, bounded check runs."""

import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.evidence import EvidenceItem
from app.models.run import CheckRun, Run, TestOutcome
from app.services.normalizer import normalize_and_bound_output
from app.services.transitions import transition_test_outcome

# Strict allowlist of permitted commands and binaries for checks
ALLOWED_CHECK_BINARIES = {"pytest", "python", "npm", "node", "cargo", "go"}

# Safe environment variables permitted into the runner process
SAFE_ENV_VARS = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "HOME",
    "USERPROFILE",
}


class CommandSecurityViolationError(Exception):
    """Raised when an unapproved binary or shell injection is detected."""

    pass


@dataclass
class CheckExecutionResult:
    check_key: str
    check_name: str
    status: str
    exit_code: int | None
    duration_ms: int
    output: str
    truncated: bool
    failure_code: str | None = None


def sanitize_runner_environment(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Strip all application secrets, database URLs, and API keys from runner environment.

    Ensures untrusted check containers/subprocesses never access host secrets.
    """
    clean_env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.upper() in SAFE_ENV_VARS:
            clean_env[key] = value

    # Explicitly ensure sensitive variables are NEVER present
    sensitive_keys = [
        "DATABASE_URL",
        "REDIS_URL",
        "SESSION_SECRET_KEY",
        "GITHUB_APP_CLIENT_SECRET",
        "GITHUB_WEBHOOK_SECRET",
        "NEBIUS_TOKEN_FACTORY_API_KEY",
    ]
    for key in sensitive_keys:
        clean_env.pop(key, None)

    if extra_env:
        for k, v in extra_env.items():
            if k not in sensitive_keys:
                clean_env[k] = v

    return clean_env


def validate_check_command(command: list[str]) -> None:
    """Enforce that check commands use an approved binary with argument arrays.

    Rejects shell injection attempts and arbitrary execution.
    """
    if not command or not isinstance(command, list):
        raise CommandSecurityViolationError("Command must be a non-empty array of strings")

    binary = os.path.basename(command[0]).lower()
    # Strip .exe on Windows
    if binary.endswith(".exe"):
        binary = binary[:-4]

    if binary not in ALLOWED_CHECK_BINARIES:
        raise CommandSecurityViolationError(
            f"Binary '{binary}' is not in the approved check allowlist: {ALLOWED_CHECK_BINARIES}"
        )

    # Reject shell chaining operators in arguments
    forbidden_operators = {"|", ";", "&", "&&", "||", "`", "$"}
    for arg in command:
        for op in forbidden_operators:
            if op in arg:
                raise CommandSecurityViolationError(
                    f"Shell metacharacter '{op}' forbidden in check arguments"
                )


def execute_isolated_check(
    check_key: str,
    check_name: str,
    command: list[str],
    cwd: str,
    timeout_seconds: int = 120,
    custom_canaries: list[str] | None = None,
) -> CheckExecutionResult:
    """Execute a single allowlisted check in a bounded, stripped environment."""
    validate_check_command(command)

    start_time = time.perf_counter()
    clean_env = sanitize_runner_environment()

    # Bound timeout to max configured limit
    max_timeout = min(timeout_seconds, settings.RUNNER_MAX_EXECUTION_TIME_SECONDS)

    try:
        proc = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=clean_env,
            timeout=max_timeout,
            shell=False,  # Critical security invariant: NEVER shell=True
        )
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")

        if proc.returncode == 0:
            status = "passed"
            failure_code = None
        else:
            status = "failed"
            failure_code = "nonzero_exit"

        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status=status,
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            output=output,
            truncated=False,
            failure_code=failure_code,
        )

    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        output = (exc.stdout or "") + "\n" + (exc.stderr or "")
        output += f"\n\n[TIMEOUT: Check exceeded {max_timeout}s execution limit]"
        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status="timed_out",
            exit_code=None,
            duration_ms=duration_ms,
            output=output,
            truncated=False,
            failure_code="check_timed_out",
        )

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status="error",
            exit_code=None,
            duration_ms=duration_ms,
            output=f"Execution error: {type(exc).__name__}",
            truncated=False,
            failure_code="runner_error",
        )


def execute_run_checks(
    db: Session,
    run: Run,
    check_definitions: list[dict[str, Any]],
    workspace_dir: str,
    custom_canaries: list[str] | None = None,
) -> TestOutcome:
    """Orchestrates execution of all configured checks for a run and persists factual results."""
    run.started_at = datetime.now(UTC)
    run.lifecycle_status = "running"
    db.commit()

    overall_outcome = TestOutcome.PASSED
    canary_list = custom_canaries or [
        settings.GITHUB_WEBHOOK_SECRET,
        settings.SESSION_SECRET_KEY,
    ]

    for check_def in check_definitions:
        check_key = check_def.get("key", "test")
        check_name = check_def.get("name", "Check")
        command = check_def.get("command", ["pytest"])
        timeout = check_def.get("timeout_seconds", 120)

        result = execute_isolated_check(
            check_key=check_key,
            check_name=check_name,
            command=command,
            cwd=workspace_dir,
            timeout_seconds=timeout,
            custom_canaries=canary_list,
        )

        # Normalize and bound output before storing
        normalized = normalize_and_bound_output(
            raw_output=result.output,
            source_label=f"{check_key}:{check_name}",
            custom_canaries=canary_list,
        )

        # Persist CheckRun record
        check_run = CheckRun(
            run_id=run.id,
            check_key=result.check_key,
            check_name=result.check_name,
            profile_version=run.check_profile_version,
            status=result.status,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            output_truncated=normalized.truncated,
            failure_code=result.failure_code,
            attempt_number=run.attempt_number,
        )
        db.add(check_run)
        db.flush()

        # Persist EvidenceItem
        evidence = EvidenceItem(
            run_id=run.id,
            check_run_id=check_run.id,
            kind=normalized.kind,
            source_label=normalized.source_label,
            content_redacted=normalized.content_redacted,
            content_digest=normalized.content_digest,
            truncated=normalized.truncated,
            byte_length=normalized.byte_length,
        )
        db.add(evidence)

        # Aggregate test outcome: any failure/error/timeout dominates pass
        if result.status == "failed" and overall_outcome == TestOutcome.PASSED:
            overall_outcome = TestOutcome.FAILED
        elif result.status == "timed_out":
            overall_outcome = TestOutcome.TIMED_OUT
        elif result.status == "error":
            overall_outcome = TestOutcome.ERROR

    # Update run outcome
    run.test_outcome = transition_test_outcome(TestOutcome(run.test_outcome), overall_outcome).value
    run.completed_at = datetime.now(UTC)
    run.lifecycle_status = "completed" if overall_outcome == TestOutcome.PASSED else "failed"
    db.commit()
    db.refresh(run)

    return overall_outcome
