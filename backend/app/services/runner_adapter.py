"""Runner adapter interface and integration layer.

Provides an explicit contract between the distributed worker architecture
and the execution runner (Phase 1 / Phase 3), enabling isolated testing and
preventing direct coupling or secret leakage.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.models.project import ProjectCheckProfile
from app.models.run import Run, TestOutcome
from app.services.runner import execute_run_checks

logger = logging.getLogger(__name__)


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


class DefaultRunnerAdapter:
    """Standard runner adapter wrapping app.services.runner.execute_run_checks.

    Preserves isolation boundaries:
    - Strips host application secrets.
    - Resolves versioned project check profile.
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

        # 2. Determine workspace directory
        # In a full worker supervisor, this is the pinned ephemeral workspace.
        # Defaults to an isolated temp directory if none provided.
        active_workspace = workspace_dir
        temp_dir: str | None = None
        if not active_workspace:
            temp_dir = tempfile.mkdtemp(prefix=f"cs-run-{run.id[:8]}-")
            active_workspace = temp_dir

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
                try:
                    import shutil
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass
