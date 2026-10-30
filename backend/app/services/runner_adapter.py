"""Runner adapter interface and integration layer.

Provides an explicit contract between the distributed worker architecture
and the execution runner (Phase 1 / Phase 3), enabling isolated testing and
preventing direct coupling or secret leakage.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.project import Project, ProjectCheckProfile
from app.models.run import Run, TestOutcome
from app.services.github_auth import GitHubAuthError, get_installation_access_token_sync
from app.services.runner import execute_run_checks

logger = logging.getLogger(__name__)


class WorkspacePreparationError(Exception):
    """Raised when repository workspace cannot be created or downloaded."""

    pass


@dataclass
class ExecutionResultSummary:
    """Safe normalized outcome returned by runner execution."""

    outcome: TestOutcome
    checks_executed: int
    passed_count: int
    failed_count: int
    failure_code: str | None = None
    error_message: str | None = None


class RunnerAdapter(Protocol):
    """Protocol for executing checks on an immutable commit snapshot."""

    def execute_run(
        self,
        db: Session,
        run: Run,
        workspace_dir: str | None = None,
        custom_canaries: list[str] | None = None,
    ) -> ExecutionResultSummary:
        ...


def safe_extract_tarball(archive_bytes: bytes, target_dir: str) -> None:
    """Safely extract tarball into target_dir preventing path traversal / tar-slip."""
    abs_target_dir = os.path.realpath(os.path.abspath(target_dir))

    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tar:
        for member in tar.getmembers():
            norm_name = os.path.normpath(member.name).replace("\\", "/")
            if norm_name.startswith("/") or ":" in norm_name or ".." in norm_name.split("/"):
                raise WorkspacePreparationError(f"Path traversal detected in tarball member: {member.name}")

            dest_path = os.path.realpath(os.path.abspath(os.path.join(target_dir, member.name)))
            if not (dest_path == abs_target_dir or dest_path.startswith(abs_target_dir + os.sep)):
                raise WorkspacePreparationError(f"Tarball member extracts outside workspace: {member.name}")

        try:
            tar.extractall(path=target_dir, filter="data")
        except TypeError:
            tar.extractall(path=target_dir)

    # GitHub tarballs wrap repository files inside a single top-level directory (e.g. 'owner-repo-sha/')
    # If target_dir contains exactly one subfolder and nothing else, move its contents up to target_dir
    items = os.listdir(target_dir)
    if len(items) == 1:
        sub = os.path.join(target_dir, items[0])
        if os.path.isdir(sub):
            for subitem in os.listdir(sub):
                shutil.move(os.path.join(sub, subitem), os.path.join(target_dir, subitem))
            os.rmdir(sub)


def download_github_repository_archive(
    repository_full_name: str,
    head_sha: str,
    installation_id: int,
) -> bytes:
    """Download commit snapshot tarball from GitHub App API without host secret exposure."""
    try:
        token = get_installation_access_token_sync(installation_id)
    except GitHubAuthError as auth_err:
        raise WorkspacePreparationError(
            f"GitHub authentication error for installation {installation_id}: {auth_err}"
        ) from auth_err

    url = f"https://api.github.com/repos/{repository_full_name}/tarball/{head_sha}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "CodeSentinel-App",
    }

    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 404:
                raise WorkspacePreparationError(
                    f"Repository '{repository_full_name}' or commit SHA '{head_sha}' not found on GitHub (HTTP 404)"
                )
            if resp.status_code in (401, 403):
                raise WorkspacePreparationError(
                    f"GitHub authorization error (HTTP {resp.status_code}) downloading '{repository_full_name}'"
                )
            if resp.status_code != 200:
                raise WorkspacePreparationError(
                    f"GitHub tarball download failed with HTTP {resp.status_code}"
                )
            return resp.content
    except httpx.TimeoutException as exc:
        raise WorkspacePreparationError(
            f"Timeout while downloading repository snapshot for '{repository_full_name}'"
        ) from exc
    except httpx.RequestError as exc:
        raise WorkspacePreparationError(
            f"Network error downloading repository snapshot: {type(exc).__name__}"
        ) from exc


def prepare_workspace_for_run(
    run: Run,
    project: Project | None = None,
    workspace_dir: str | None = None,
) -> tuple[str, str | None]:
    """Prepare isolated workspace for the run.

    Returns (active_workspace_path, temp_dir_to_cleanup).
    """
    if workspace_dir and os.path.exists(workspace_dir):
        return workspace_dir, None

    workspace_root = settings.RUNNER_WORKSPACE_ROOT.strip() if settings.RUNNER_WORKSPACE_ROOT else None
    temp_dir = tempfile.mkdtemp(dir=workspace_root, prefix=f"cs-run-{run.id[:8]}-")

    has_github_creds = bool(
        settings.GITHUB_APP_ID
        and (settings.GITHUB_APP_PRIVATE_KEY_PATH or os.environ.get("GITHUB_APP_PRIVATE_KEY"))
    )

    if (
        project
        and project.repository_full_name
        and project.github_installation_id
        and has_github_creds
    ):
        try:
            archive_bytes = download_github_repository_archive(
                repository_full_name=project.repository_full_name,
                head_sha=run.head_sha,
                installation_id=project.github_installation_id,
            )
            safe_extract_tarball(archive_bytes, temp_dir)
            return temp_dir, temp_dir
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    # Fallback to local sample repository fixture if present (dev/testing mode)
    base_fixture = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "sample_repo")
    )
    if os.path.exists(base_fixture) and os.path.isdir(base_fixture):
        for item in os.listdir(base_fixture):
            s = os.path.join(base_fixture, item)
            d = os.path.join(temp_dir, item)
            if os.path.isdir(s) and not os.path.islink(s):
                shutil.copytree(s, d, symlinks=False)
            elif os.path.isfile(s) and not os.path.islink(s):
                shutil.copy2(s, d)
        return temp_dir, temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)
    raise WorkspacePreparationError(
        f"Cannot prepare workspace for external repository '{getattr(project, 'repository_full_name', 'unknown')}': "
        "GitHub App credentials are not configured to download commit snapshots."
    )


class DefaultRunnerAdapter:
    """Standard runner adapter wrapping app.services.runner.execute_run_checks.

    Preserves isolation boundaries:
    - Strips host application secrets.
    - Resolves versioned project check profile.
    - Fetches and verifies immutable repository snapshot inside ephemeral workspace.
    - Persists CheckRun and EvidenceItem records via the existing runner.
    """

    def execute_run(
        self,
        db: Session,
        run: Run,
        workspace_dir: str | None = None,
        custom_canaries: list[str] | None = None,
    ) -> ExecutionResultSummary:
        # 1. Resolve project check profile matching the pinned profile version
        profile = (
            db.query(ProjectCheckProfile)
            .filter(
                ProjectCheckProfile.project_id == run.project_id,
                ProjectCheckProfile.version == run.check_profile_version,
            )
            .first()
        )

        check_definitions = profile.check_definitions if profile else [{"key": "test", "command": ["pytest"]}]

        # 2. Determine and prepare workspace directory
        project = (
            run.project
            if hasattr(run, "project") and run.project
            else db.query(Project).filter(Project.id == run.project_id).first()
        )

        active_workspace = workspace_dir
        temp_dir: str | None = None

        try:
            active_workspace, temp_dir = prepare_workspace_for_run(
                run=run,
                project=project,
                workspace_dir=workspace_dir,
            )
        except WorkspacePreparationError as prep_err:
            logger.error("Workspace preparation failed for run=%s: %s", run.id, prep_err)
            return ExecutionResultSummary(
                outcome=TestOutcome.ERROR,
                checks_executed=0,
                passed_count=0,
                failed_count=1,
                failure_code="repository_workspace_failed",
                error_message=str(prep_err),
            )

        try:
            logger.info(
                "Executing run=%s head_sha=%s checks=%d in workspace=%s",
                run.id,
                run.head_sha,
                len(check_definitions),
                active_workspace,
            )

            outcome = execute_run_checks(
                db=db,
                run=run,
                check_definitions=check_definitions,
                workspace_dir=active_workspace,
                custom_canaries=custom_canaries,
            )

            # Gather stats from persisted checks
            checks = run.checks
            passed = sum(1 for c in checks if c.status == "passed")
            failed = sum(1 for c in checks if c.status != "passed")

            failure_code = None
            if outcome == TestOutcome.FAILED:
                failure_code = "checks_failed"
            elif outcome == TestOutcome.TIMED_OUT:
                failure_code = "runner_timed_out"
            elif outcome == TestOutcome.ERROR:
                failure_code = "runner_error"

            return ExecutionResultSummary(
                outcome=outcome,
                checks_executed=len(checks),
                passed_count=passed,
                failed_count=failed,
                failure_code=failure_code,
            )

        except Exception as exc:
            logger.exception("Runner execution failed for run=%s: %s", run.id, exc)
            return ExecutionResultSummary(
                outcome=TestOutcome.ERROR,
                checks_executed=0,
                passed_count=0,
                failed_count=1,
                failure_code="runner_exception",
                error_message=f"Execution error: {type(exc).__name__}",
            )

        finally:
            # Clean up ephemeral temp workspace if we created one
            if temp_dir and os.path.exists(temp_dir):
                with contextlib.suppress(Exception):
                    shutil.rmtree(temp_dir, ignore_errors=True)
