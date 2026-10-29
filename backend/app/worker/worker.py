"""Distributed background worker for CodeSentinel.

Consumes queued jobs, handles retries, bounds timeouts, and ensures deterministic
test and analysis execution isolated from HTTP request lifecycles.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.job import JobType
from app.models.run import Run, TestOutcome
from app.services.queue import (
    claim_next_job,
    complete_job,
    fail_job,
    recover_interrupted_jobs,
    timeout_job,
)
from app.services.runner_adapter import DefaultRunnerAdapter, RunnerAdapter

logger = logging.getLogger("codesentinel.worker")


class Worker:
    """Independent worker daemon consuming durable queue jobs."""

    def __init__(
        self,
        worker_id: str | None = None,
        poll_interval: float | None = None,
        runner_adapter: RunnerAdapter | None = None,
        db_factory: Callable[[], Session] | None = None,
        workspace_dir: str | None = None,
        max_jobs: int | None = None,
    ):
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        base_interval = (
            poll_interval
            if poll_interval is not None
            else settings.WORKER_POLL_INTERVAL_SECONDS
        )
        self.poll_interval = max(0.01, base_interval)
        self.runner_adapter = runner_adapter or DefaultRunnerAdapter()
        self.db_factory = db_factory or SessionLocal
        self.workspace_dir = workspace_dir
        self.max_jobs = max_jobs
        self.jobs_processed = 0

        self._stop_event = threading.Event()
        self._is_running = False
        self._last_recovery_time = 0.0

    @property
    def is_running(self) -> bool:
        return self._is_running

    def stop(self) -> None:
        """Signal the worker to stop gracefully after current job completes."""
        logger.info("Worker %s stop requested", self.worker_id)
        self._stop_event.set()

    def process_one_job(self) -> bool:
        """Attempt to claim and process a single eligible job.

        Returns True if a job was found and processed, False if queue was empty.
        Ensures database transactions are NOT held open during command execution.
        """
        # 1. Claim job atomically in a short transaction
        with self.db_factory() as db:
            job = claim_next_job(db, self.worker_id)

        if not job:
            return False

        job_id = job.id
        job_type = job.job_type
        run_id = job.run_id
        timeout_seconds = job.timeout_seconds

        logger.info(
            "Worker %s claimed job=%s (type=%s, run_id=%s, attempt=%d)",
            self.worker_id,
            job_id,
            job_type,
            run_id,
            job.attempt_number,
        )

        # 2. Execute workload outside the claiming transaction
        try:
            if job_type == JobType.SCAN.value:
                self._execute_scan_job(job_id, run_id, timeout_seconds)
            elif job_type == JobType.ANALYSIS.value:
                self._execute_analysis_job(job_id, run_id)
            else:
                with self.db_factory() as db:
                    fail_job(
                        db,
                        job_id,
                        error_message=f"Unknown job type: {job_type}",
                        failure_code="unknown_job_type",
                        is_transient=False,
                    )

        except TimeoutError as exc:
            logger.warning("Job %s timed out: %s", job_id, exc)
            with self.db_factory() as db:
                timeout_job(db, job_id, error_message=str(exc))

        except Exception as exc:
            logger.exception("Unexpected worker error on job %s: %s", job_id, exc)
            with self.db_factory() as db:
                fail_job(
                    db,
                    job_id,
                    error_message=f"Worker exception: {type(exc).__name__}",
                    failure_code="worker_execution_error",
                    is_transient=True,  # Transient infrastructure failure triggers bounded retry
                )

        self.jobs_processed += 1
        return True

    def _execute_scan_job(self, job_id: str, run_id: str | None, timeout_seconds: int) -> None:
        """Executes test runner checks for a scan job."""
        if not run_id:
            with self.db_factory() as db:
                fail_job(
                    db,
                    job_id,
                    error_message="Scan job missing required run_id",
                    failure_code="missing_run_id",
                    is_transient=False,
                )
            return

        with self.db_factory() as run_db:
            run = run_db.query(Run).filter(Run.id == run_id).first()
            if not run:
                with self.db_factory() as db:
                    fail_job(
                        db,
                        job_id,
                        error_message=f"Run {run_id} not found",
                        failure_code="run_not_found",
                        is_transient=False,
                    )
                return

            # Execute via runner adapter
            summary = self.runner_adapter.execute_run(
                db=run_db,
                run=run,
                workspace_dir=self.workspace_dir,
            )

        # Update job outcome based on deterministic test results
        with self.db_factory() as db:
            if summary.outcome == TestOutcome.PASSED:
                complete_job(
                    db,
                    job_id,
                    result={
                        "outcome": summary.outcome.value,
                        "checks_executed": summary.checks_executed,
                        "passed_count": summary.passed_count,
                        "failed_count": summary.failed_count,
                    },
                )
            elif summary.outcome == TestOutcome.TIMED_OUT:
                timeout_job(
                    db,
                    job_id,
                    error_message=summary.error_message or "Runner checks exceeded execution timeout",
                )
            else:
                # Failing checks are factual test outcomes, not transient queue retries
                fail_job(
                    db,
                    job_id,
                    error_message=summary.error_message or f"Checks finished with outcome: {summary.outcome.value}",
                    failure_code=summary.failure_code or "checks_failed",
                    is_transient=False,
                )

    def _execute_analysis_job(self, job_id: str, run_id: str | None) -> None:
        """Executes AI diagnosis for an analysis job."""
        if not run_id:
            with self.db_factory() as db:
                fail_job(
                    db,
                    job_id,
                    error_message="Analysis job missing run_id",
                    failure_code="missing_run_id",
                    is_transient=False,
                )
            return

        from app.services.analysis import run_analysis

        with self.db_factory() as db:
            run = db.query(Run).filter(Run.id == run_id).first()
            analysis = (
                db.query(Analysis)
                .filter(Analysis.run_id == run_id)
                .order_by(Analysis.attempt_number.desc())
                .first()
            )

            if not run or not analysis:
                fail_job(
                    db,
                    job_id,
                    error_message="Run or Analysis record not found for analysis job",
                    failure_code="record_not_found",
                    is_transient=False,
                )
                return

            run_analysis(run, analysis, db)

            # Re-read analysis state after execution to determine accurate outcome
            db.refresh(analysis)
            if analysis.status == "succeeded":
                complete_job(
                    db,
                    job_id,
                    result={"analysis_id": analysis.id, "status": analysis.status},
                )
            else:
                fail_job(
                    db,
                    job_id,
                    error_message=f"Analysis completed with non-success status: {analysis.status}",
                    failure_code=getattr(analysis, "safe_error_code", None) or "analysis_failed",
                    is_transient=False,
                )

    def run(self) -> None:
        """Main worker loop with graceful shutdown, crash resilience, and periodic recovery."""
        self._is_running = True
        logger.info(
            "Worker %s started (poll_interval=%.1fs, timeout=%ds)",
            self.worker_id,
            self.poll_interval,
            settings.WORKER_DEFAULT_JOB_TIMEOUT_SECONDS,
        )

        try:
            while not self._stop_event.is_set():
                try:
                    # Periodic recovery of abandoned/interrupted jobs
                    now_sec = time.monotonic()
                    if (
                        now_sec - self._last_recovery_time
                        > settings.WORKER_INTERRUPTED_RECOVERY_INTERVAL_SECONDS
                    ):
                        try:
                            with self.db_factory() as db:
                                recover_interrupted_jobs(db)
                            self._last_recovery_time = now_sec
                        except Exception as exc:
                            logger.warning("Interrupted job recovery check failed: %s", exc)

                    # Process next eligible job
                    found_job = self.process_one_job()

                    if self.max_jobs and self.jobs_processed >= self.max_jobs:
                        logger.info("Worker reached max_jobs limit (%d)", self.max_jobs)
                        break

                    if not found_job:
                        # Non-busy wait
                        self._stop_event.wait(self.poll_interval)

                except Exception as loop_exc:
                    # Guard worker daemon against unhandled transient errors (DB blips, timeouts)
                    logger.error("Transient error in worker %s loop: %s", self.worker_id, loop_exc)
                    self._stop_event.wait(self.poll_interval)

        finally:
            self._is_running = False
            logger.info(
                "Worker %s stopped gracefully after processing %d jobs",
                self.worker_id,
                self.jobs_processed,
            )
