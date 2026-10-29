"""Execution supervisor for isolated, bounded check runs.

Security architecture and controls:
----------------------------------
1. Container Isolation:
   - Checks execute inside disposable, ephemeral Docker or Podman containers.
   - Fail-closed: if container runtime is selected and unavailable, execution halts
     with status='error' and failure_code='container_runtime_unavailable'.
     Host execution fallback is strictly prohibited when container runtime is configured.
   - Network disabled by default (--network none).
   - Enforced memory (--memory), CPU (--cpus), PID (--pids-limit) quotas.
   - Dropped Linux capabilities (--cap-drop=ALL) and --security-opt=no-new-privileges.
   - Read-only root filesystem (--read-only) with isolated tmpfs mount (/tmp).
   - Non-root user identity (--user 10001:10001).
   - Only the validated workspace is mounted (-v <cwd>:/workspace:rw) with working dir /workspace.
   - Ephemeral lifecycle: containers are explicitly stopped and removed on exit, timeout, or error.
   - Zero secrets injected: only generic locale and scratch path environment variables forwarded.

2. Command Execution Safety:
   - Validated command array against approved binary allowlist (pytest, python, npm, node, cargo, go).
   - Shell metacharacters (|, ;, &, &&, ||, `, $), newlines, carriage returns, and null bytes rejected.
   - shell=False strictly enforced; no shell interpreters.
   - Direct process invocation using argument arrays.

3. Path & Workspace Integrity:
   - Canonical path resolution (os.path.realpath) preventing path traversal (../) or directory escapes.
   - symlink dereferencing checks ensuring workspace remains within allowed root.

4. Streaming Bounded Output:
   - Concurrently reads stdout/stderr in 8 KB chunks with thread synchronization.
   - Memory retention capped at RUNNER_MAX_OUTPUT_BYTES during streaming.
   - Excess bytes are discarded from the stream to prevent host OOM without deadlocking child pipes.

5. Reliability & Truth Invariants:
   - Timeout terminates container/process tree immediately.
   - Timeouts, crashes, missing binaries, and errors are NEVER converted to passed checks.
   - CheckExecutionResult contract preserved for Phase 2 worker decoupling.
"""

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.evidence import EvidenceItem
from app.models.run import CheckRun, Run, TestOutcome
from app.services.normalizer import normalize_and_bound_output
from app.services.transitions import transition_test_outcome

logger = logging.getLogger(__name__)

# Strict allowlist of permitted commands and binaries for checks
ALLOWED_CHECK_BINARIES = {"pytest", "python", "npm", "node", "cargo", "go"}

# Safe environment variables permitted into the runner process (subprocess mode).
# PYTHONPATH is deliberately excluded: a workspace-controlled PYTHONPATH could
# shadow stdlib or inject attacker modules before the subprocess starts.
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
    # PYTHONPATH intentionally absent
    "HOME",
    "USERPROFILE",
}


class CommandSecurityViolationError(Exception):
    """Raised when an unapproved binary or shell injection is detected."""

    pass


class WorkspaceViolationError(Exception):
    """Raised when a provided workspace path is unsafe or traverses outside expected root."""

    pass


class ContainerRuntimeUnavailableError(Exception):
    """Raised when container execution is required but runtime or daemon is unavailable."""

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
    execution_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    runtime: str = "container"


def sanitize_runner_environment(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Strip all application secrets, database URLs, and API keys from runner environment.

    Ensures untrusted check containers/subprocesses never access host secrets.
    Only variables in SAFE_ENV_VARS allowlist are forwarded; all others are dropped.
    """
    clean_env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.upper() in SAFE_ENV_VARS:
            clean_env[key] = value

    sensitive_keys = [
        "DATABASE_URL",
        "REDIS_URL",
        "SESSION_SECRET_KEY",
        "GITHUB_APP_CLIENT_SECRET",
        "GITHUB_WEBHOOK_SECRET",
        "NEBIUS_TOKEN_FACTORY_API_KEY",
        "GITHUB_APP_PRIVATE_KEY",
        "PYTHONPATH",
    ]
    for key in sensitive_keys:
        clean_env.pop(key, None)

    if extra_env:
        for k, v in extra_env.items():
            if k not in sensitive_keys and k.upper() not in sensitive_keys:
                clean_env[k] = v

    return clean_env


def validate_check_command(command: list[str]) -> None:
    """Enforce that check commands use an approved binary with argument arrays.

    Rejects shell injection attempts, metacharacters, and arbitrary execution.
    """
    if not command or not isinstance(command, list):
        raise CommandSecurityViolationError("Command must be a non-empty array of strings")

    if not all(isinstance(arg, str) for arg in command):
        raise CommandSecurityViolationError("All command arguments must be strings")

    binary = os.path.basename(command[0]).lower()
    if binary.endswith(".exe"):
        binary = binary[:-4]

    if binary not in ALLOWED_CHECK_BINARIES:
        raise CommandSecurityViolationError(
            f"Binary '{binary}' is not in the approved check allowlist: {ALLOWED_CHECK_BINARIES}"
        )

    # Reject shell chaining operators, subshells, newlines, null bytes in arguments
    forbidden_operators = {"|", ";", "&", "&&", "||", "`", "$", "\n", "\r", "\0"}
    for arg in command:
        for op in forbidden_operators:
            if op in arg:
                raise CommandSecurityViolationError(
                    f"Shell metacharacter '{repr(op)}' forbidden in check arguments"
                )


def validate_workspace_path(cwd: str, allowed_base: str | None = None) -> str:
    """Resolve and validate that the workspace path is safe.

    Returns the canonical resolved absolute path.
    Raises WorkspaceViolationError if path does not exist, is not a directory,
    or traverses outside allowed_base.
    """
    if not cwd or not isinstance(cwd, str):
        raise WorkspaceViolationError("Workspace path must be a non-empty string")

    try:
        resolved = os.path.realpath(os.path.abspath(cwd))
    except (OSError, ValueError) as exc:
        raise WorkspaceViolationError(f"Cannot resolve workspace path: {exc}") from exc

    if not os.path.exists(resolved):
        raise WorkspaceViolationError(f"Workspace path does not exist: {resolved}")

    if not os.path.isdir(resolved):
        raise WorkspaceViolationError(f"Workspace path is not a directory: {resolved}")

    if allowed_base is not None and allowed_base.strip():
        try:
            base_resolved = os.path.realpath(os.path.abspath(allowed_base))
        except (OSError, ValueError) as exc:
            raise WorkspaceViolationError(f"Cannot resolve allowed base path: {exc}") from exc

        if not os.path.exists(base_resolved) or not os.path.isdir(base_resolved):
            raise WorkspaceViolationError(
                f"Allowed base directory does not exist or is not a directory: {base_resolved}"
            )

        norm_resolved = os.path.normcase(resolved)
        norm_base = os.path.normcase(base_resolved)

        try:
            common = os.path.commonpath([norm_resolved, norm_base])
            is_contained = common == norm_base
        except (ValueError, OSError):
            is_contained = False

        if not is_contained:
            raise WorkspaceViolationError(
                f"Workspace path escapes allowed base directory: {resolved!r} not under {base_resolved!r}"
            )

    return resolved


def is_container_runtime_available(runtime: str = "docker") -> bool:
    """Check whether the specified container runtime (docker/podman) is installed and functional."""
    bin_path = shutil.which(runtime)
    if not bin_path:
        return False
    try:
        # Check that runtime daemon or engine responds
        res = subprocess.run(
            [runtime, "info"],
            capture_output=True,
            timeout=3,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def build_container_command(
    runtime: str,
    image: str,
    container_name: str,
    host_workspace: str,
    check_command: list[str],
    network_enabled: bool = False,
    memory_limit: str = "1g",
    cpu_limit: float = 1.0,
    pid_limit: int = 64,
    tmpfs_size: str = "64m",
    read_only_root: bool = True,
    user: str = "10001:10001",
    cap_drop_all: bool = True,
    no_new_privileges: bool = True,
) -> list[str]:
    """Construct hardened container execution argument array with security boundary flags."""
    cmd = [runtime, "run", "--name", container_name]

    # Network isolation: default-deny
    if not network_enabled:
        cmd.extend(["--network", "none"])

    # Resource limits: memory, CPU, PID bounds
    cmd.extend(["--memory", memory_limit])
    cmd.extend(["--cpus", str(cpu_limit)])
    cmd.extend(["--pids-limit", str(pid_limit)])

    # Linux security profile: drop all capabilities, prevent privilege escalation
    if cap_drop_all:
        cmd.append("--cap-drop=ALL")
    if no_new_privileges:
        cmd.append("--security-opt=no-new-privileges")

    # Non-root user identity
    if user:
        cmd.extend(["--user", user])

    # Read-only root filesystem with scoped tmpfs
    if read_only_root:
        cmd.append("--read-only")
        cmd.extend(["--tmpfs", f"/tmp:rw,noexec,nosuid,size={tmpfs_size}"])

    # Mount only the requested workspace directory to /workspace
    cmd.extend(["-v", f"{host_workspace}:/workspace:rw"])
    cmd.extend(["-w", "/workspace"])

    # Safe environment variables inside container (zero host/app secrets)
    cmd.extend([
        "-e", "LANG=C.UTF-8",
        "-e", "LC_ALL=C.UTF-8",
        "-e", "HOME=/tmp",
        "-e", "TMPDIR=/tmp",
    ])

    # Container image
    cmd.append(image)

    # Allowlisted check command (canonicalized binary name inside container)
    binary = os.path.basename(check_command[0]).lower()
    if binary.endswith(".exe"):
        binary = binary[:-4]
    cmd.extend([binary] + check_command[1:])

    return cmd


def _cleanup_container(runtime_bin: str, container_name: str) -> None:
    """Force remove an execution container to prevent resource or container leaks."""
    try:
        subprocess.run(
            [runtime_bin, "rm", "-f", container_name],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception as exc:
        logger.warning("Container cleanup failed for %s: %s", container_name, exc)


def _stop_container(runtime_bin: str, container_name: str) -> None:
    """Terminate a running container."""
    try:
        subprocess.run(
            [runtime_bin, "kill", container_name],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        logger.warning("Container kill failed for %s: %s", container_name, exc)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Terminate the process and its child processes across POSIX and Windows."""
    try:
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                proc.kill()
        elif os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                proc.kill()
        else:
            proc.kill()
    except Exception as exc:
        logger.warning("Process tree kill attempt failed (pid=%s): %s", proc.pid, exc)


def _read_bounded_process_output(
    proc: subprocess.Popen,
    max_bytes: int,
    timeout_seconds: float,
) -> tuple[str, bool, bool]:
    """Read stdout and stderr concurrently with strict streaming memory bounding.

    Returns: (output_str, is_truncated, is_timed_out)

    Security property:
    Buffers at most max_bytes in memory. Once max_bytes is reached, excess bytes
    are drained and dropped to prevent OOM without deadlocking OS pipe buffers.
    """
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    total_captured = [0]
    truncated = [False]
    lock = threading.Lock()

    def _stream_drain(stream, target_list):
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                with lock:
                    if total_captured[0] < max_bytes:
                        allowed = max_bytes - total_captured[0]
                        target_list.append(chunk[:allowed])
                        total_captured[0] += len(chunk[:allowed])
                        if len(chunk) > allowed:
                            truncated[0] = True
                    else:
                        truncated[0] = True
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    t_stdout = threading.Thread(
        target=_stream_drain, args=(proc.stdout, stdout_chunks), daemon=True
    )
    t_stderr = threading.Thread(
        target=_stream_drain, args=(proc.stderr, stderr_chunks), daemon=True
    )
    t_stdout.start()
    t_stderr.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc)

    t_stdout.join(timeout=3)
    t_stderr.join(timeout=3)

    out_bytes = b"".join(stdout_chunks)
    err_bytes = b"".join(stderr_chunks)
    combined = out_bytes + (b"\n" + err_bytes if err_bytes else b"")

    if truncated[0]:
        truncation_marker = (
            f"\n\n[RUNNER: Output truncated at {max_bytes} bytes by runner output limit]"
        ).encode()
        combined = combined[:max_bytes] + truncation_marker

    output_str = combined.decode("utf-8", errors="replace")
    return output_str, truncated[0], timed_out


def _execute_in_container(
    runtime: str,
    check_key: str,
    check_name: str,
    command: list[str],
    workspace_dir: str,
    timeout_seconds: int,
    execution_id: str,
) -> CheckExecutionResult:
    """Execute check inside a restricted disposable container."""
    container_name = f"codesentinel-check-{execution_id[:16]}"
    max_timeout = min(timeout_seconds, settings.RUNNER_MAX_EXECUTION_TIME_SECONDS)
    max_output_bytes = settings.RUNNER_MAX_OUTPUT_BYTES

    container_cmd = build_container_command(
        runtime=runtime,
        image=settings.RUNNER_CONTAINER_IMAGE,
        container_name=container_name,
        host_workspace=workspace_dir,
        check_command=command,
        network_enabled=settings.RUNNER_NETWORK_ENABLED,
        memory_limit=settings.RUNNER_CONTAINER_MEMORY_LIMIT,
        cpu_limit=settings.RUNNER_CONTAINER_CPU_LIMIT,
        pid_limit=settings.RUNNER_CONTAINER_PID_LIMIT,
        tmpfs_size=settings.RUNNER_CONTAINER_TMPFS_SIZE,
        read_only_root=settings.RUNNER_CONTAINER_READ_ONLY_ROOT,
        user=settings.RUNNER_CONTAINER_USER,
        cap_drop_all=settings.RUNNER_CONTAINER_CAP_DROP_ALL,
        no_new_privileges=settings.RUNNER_CONTAINER_NO_NEW_PRIVILEGES,
    )

    logger.info(
        "Container runner starting execution_id=%s container=%s image=%s timeout=%ds",
        execution_id,
        container_name,
        settings.RUNNER_CONTAINER_IMAGE,
        max_timeout,
    )

    start_time = time.perf_counter()
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            container_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            shell=False,
        )

        output, truncated, timed_out = _read_bounded_process_output(
            proc=proc,
            max_bytes=max_output_bytes,
            timeout_seconds=float(max_timeout),
        )
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        if timed_out:
            _stop_container(runtime, container_name)
            timeout_msg = f"\n\n[TIMEOUT: Container check exceeded {max_timeout}s limit; container terminated]"
            return CheckExecutionResult(
                check_key=check_key,
                check_name=check_name,
                status="timed_out",
                exit_code=None,
                duration_ms=duration_ms,
                output=output + timeout_msg,
                truncated=truncated,
                failure_code="check_timed_out",
                execution_id=execution_id,
                runtime=runtime,
            )

        exit_code = proc.returncode
        status = "passed" if exit_code == 0 else "failed"
        failure_code = None if exit_code == 0 else "nonzero_exit"

        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            output=output,
            truncated=truncated,
            failure_code=failure_code,
            execution_id=execution_id,
            runtime=runtime,
        )

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        _stop_container(runtime, container_name)
        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status="error",
            exit_code=None,
            duration_ms=duration_ms,
            output=f"Container execution error: {type(exc).__name__}: {exc}",
            truncated=False,
            failure_code="runner_error",
            execution_id=execution_id,
            runtime=runtime,
        )
    finally:
        _cleanup_container(runtime, container_name)


def _execute_in_subprocess(
    check_key: str,
    check_name: str,
    command: list[str],
    workspace_dir: str,
    timeout_seconds: int,
    execution_id: str,
) -> CheckExecutionResult:
    """Execute check in local host subprocess (used only when explicitly configured)."""
    max_timeout = min(timeout_seconds, settings.RUNNER_MAX_EXECUTION_TIME_SECONDS)
    max_output_bytes = settings.RUNNER_MAX_OUTPUT_BYTES
    clean_env = sanitize_runner_environment()

    logger.info(
        "Subprocess runner starting execution_id=%s check_key=%s timeout=%ds",
        execution_id,
        check_key,
        max_timeout,
    )

    start_time = time.perf_counter()
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            command,
            cwd=workspace_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            env=clean_env,
            shell=False,
            start_new_session=True,
        )

        output, truncated, timed_out = _read_bounded_process_output(
            proc=proc,
            max_bytes=max_output_bytes,
            timeout_seconds=float(max_timeout),
        )
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        if timed_out:
            timeout_msg = f"\n\n[TIMEOUT: Check exceeded {max_timeout}s execution limit; process tree terminated]"
            return CheckExecutionResult(
                check_key=check_key,
                check_name=check_name,
                status="timed_out",
                exit_code=None,
                duration_ms=duration_ms,
                output=output + timeout_msg,
                truncated=truncated,
                failure_code="check_timed_out",
                execution_id=execution_id,
                runtime="subprocess",
            )

        exit_code = proc.returncode
        status = "passed" if exit_code == 0 else "failed"
        failure_code = None if exit_code == 0 else "nonzero_exit"

        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            output=output,
            truncated=truncated,
            failure_code=failure_code,
            execution_id=execution_id,
            runtime="subprocess",
        )

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start_time) * 1000)
        if proc is not None:
            _kill_process_tree(proc)

        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status="error",
            exit_code=None,
            duration_ms=duration_ms,
            output=f"Execution error: {type(exc).__name__}",
            truncated=False,
            failure_code="runner_error",
            execution_id=execution_id,
            runtime="subprocess",
        )


def execute_isolated_check(
    check_key: str,
    check_name: str,
    command: list[str],
    cwd: str,
    timeout_seconds: int = 120,
    custom_canaries: list[str] | None = None,
    allowed_base: str | None = None,
) -> CheckExecutionResult:
    """Primary execution interface: executes a single allowlisted check.

    Routing & Safety:
    - Validates workspace containment against trusted root (allowed_base or settings.RUNNER_WORKSPACE_ROOT).
    - If RUNNER_CONTAINER_RUNTIME is 'docker' or 'podman':
      Fails closed if runtime is not available (NEVER silently falls back to host execution).
    - If RUNNER_CONTAINER_RUNTIME is 'subprocess':
      Runs in stripped host subprocess (strictly when explicitly configured).
    """
    execution_id = uuid.uuid4().hex
    validate_check_command(command)

    runtime_choice = settings.RUNNER_CONTAINER_RUNTIME.lower()

    if runtime_choice == "subprocess" and settings.CODESENTINEL_ENV == "production":
        logger.error("Subprocess execution is strictly forbidden in production. Failing closed.")
        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status="error",
            exit_code=None,
            duration_ms=0,
            output="Security violation: Host subprocess execution is strictly disabled in production.",
            truncated=False,
            failure_code="production_subprocess_forbidden",
            execution_id=execution_id,
            runtime="subprocess",
        )

    trusted_root = settings.RUNNER_WORKSPACE_ROOT.strip() if settings.RUNNER_WORKSPACE_ROOT else None

    # If allowed_base is provided by a caller, ensure it cannot escape trusted_root
    if allowed_base and allowed_base.strip():
        if trusted_root:
            # Validate that caller-supplied allowed_base is strictly within trusted_root
            try:
                allowed_base = validate_workspace_path(allowed_base, allowed_base=trusted_root)
            except WorkspaceViolationError as exc:
                return CheckExecutionResult(
                    check_key=check_key,
                    check_name=check_name,
                    status="error",
                    exit_code=None,
                    duration_ms=0,
                    output=f"Security violation: caller-supplied allowed_base escapes trusted RUNNER_WORKSPACE_ROOT: {exc}",
                    truncated=False,
                    failure_code="workspace_violation",
                    execution_id=execution_id,
                    runtime=runtime_choice,
                )
        effective_allowed_base = allowed_base
    else:
        effective_allowed_base = trusted_root

    if settings.CODESENTINEL_ENV == "production" and not effective_allowed_base:
        logger.error("Workspace boundary containment error: RUNNER_WORKSPACE_ROOT is not configured in production.")
        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status="error",
            exit_code=None,
            duration_ms=0,
            output="Security violation: RUNNER_WORKSPACE_ROOT must be configured in production to enforce workspace boundaries.",
            truncated=False,
            failure_code="workspace_violation",
            execution_id=execution_id,
            runtime=runtime_choice,
        )

    try:
        resolved_cwd = validate_workspace_path(cwd, allowed_base=effective_allowed_base)
    except WorkspaceViolationError as exc:
        return CheckExecutionResult(
            check_key=check_key,
            check_name=check_name,
            status="error",
            exit_code=None,
            duration_ms=0,
            output=f"Workspace validation error: {exc}",
            truncated=False,
            failure_code="workspace_violation",
            execution_id=execution_id,
            runtime=runtime_choice,
        )

    if runtime_choice in ("docker", "podman"):
        if (
            not is_container_runtime_available(runtime_choice)
            and settings.RUNNER_FAIL_CLOSED_ON_MISSING_RUNTIME
        ):
            logger.error(
                "Container runtime '%s' requested but unavailable. Failing closed; host fallback disabled.",
                runtime_choice,
            )
            return CheckExecutionResult(
                check_key=check_key,
                check_name=check_name,
                status="error",
                exit_code=None,
                duration_ms=0,
                output=(
                    f"Container execution failed closed: runtime '{runtime_choice}' is not available "
                    f"or daemon is unreachable. Host execution fallback is strictly disabled."
                ),
                truncated=False,
                failure_code="container_runtime_unavailable",
                execution_id=execution_id,
                runtime=runtime_choice,
            )
        return _execute_in_container(
            runtime=runtime_choice,
            check_key=check_key,
            check_name=check_name,
            command=command,
            workspace_dir=resolved_cwd,
            timeout_seconds=timeout_seconds,
            execution_id=execution_id,
        )
    elif runtime_choice == "subprocess":
        if settings.CODESENTINEL_ENV == "production":
            logger.error("Subprocess execution is strictly forbidden in production. Failing closed.")
            return CheckExecutionResult(
                check_key=check_key,
                check_name=check_name,
                status="error",
                exit_code=None,
                duration_ms=0,
                output="Security violation: Host subprocess execution is strictly disabled in production.",
                truncated=False,
                failure_code="production_subprocess_forbidden",
                execution_id=execution_id,
                runtime="subprocess",
            )
        return _execute_in_subprocess(
            check_key=check_key,
            check_name=check_name,
            command=command,
            workspace_dir=resolved_cwd,
            timeout_seconds=timeout_seconds,
            execution_id=execution_id,
        )
    else:
        raise ValueError(f"Unsupported RUNNER_CONTAINER_RUNTIME: {runtime_choice}")



def execute_run_checks(
    db: Session,
    run: Run,
    check_definitions: list[dict[str, Any]],
    workspace_dir: str,
    custom_canaries: list[str] | None = None,
    allowed_base: str | None = None,
) -> TestOutcome:
    """Orchestrates execution of all configured checks for a run and persists factual results.

    Phase 2 interface contract:
    - Signature is stable: worker.py may call this directly after importing from runner.
    - db, run, check_definitions, workspace_dir, custom_canaries are all required.
    - Returns TestOutcome (the authoritative factual result — never influenced by model output).
    - All individual check results are persisted as CheckRun + EvidenceItem records.
    - Status is accurately mapped: timeout → TIMED_OUT, error → ERROR, etc.
      No missing or incomplete check is ever reported as PASSED.
    """
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
            allowed_base=allowed_base,
        )

        try:
            from app.core.telemetry import record_check_execution

            tool = command[0] if (command and isinstance(command, list)) else "check"
            record_check_execution(tool, result.status, result.duration_ms / 1000.0)
        except Exception:
            pass

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
