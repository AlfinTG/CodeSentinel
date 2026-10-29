"""Durable transactional queue service for distributed background workers.

Provides atomic job claiming, exponential backoff retries, idempotency protection,
and interrupted-job recovery using database-level locking.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import Job, JobStatus, JobType
from app.models.run import AnalysisOutcome, LifecycleStatus, Run, TestOutcome
from app.services.transitions import transition_job_status, transition_lifecycle

logger = logging.getLogger(__name__)


class DuplicateJobError(Exception):
    """Raised when an idempotency key conflict occurs."""

    pass


def _as_utc(dt: datetime | None) -> datetime | None:
    """Helper to ensure datetime is UTC timezone-aware across SQLite and PostgreSQL."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def enqueue_job(
    db: Session,
    project_id: str,
    job_type: JobType | str = JobType.SCAN,
    run_id: str | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    max_attempts: int | None = None,
    retry_delay_seconds: int | None = None,
    priority: int = 0,
    timeout_seconds: int | None = None,
) -> Job:
    """Transactionally enqueue a durable background job with idempotency protection.

    If an idempotency_key is provided and a job with that key already exists,
    the existing job is returned without creating duplicates.
    """
    type_str = job_type.value if isinstance(job_type, JobType) else str(job_type)
    eff_max_attempts = max_attempts if max_attempts is not None else settings.WORKER_DEFAULT_MAX_ATTEMPTS
    eff_retry_delay = retry_delay_seconds if retry_delay_seconds is not None else settings.WORKER_DEFAULT_RETRY_DELAY_SECONDS
    eff_timeout = timeout_seconds if timeout_seconds is not None else settings.WORKER_DEFAULT_JOB_TIMEOUT_SECONDS

    # 1. Check idempotency barrier
    if idempotency_key:
        existing = db.query(Job).filter(Job.idempotency_key == idempotency_key).first()
        if existing:
            logger.info(
                "Idempotent job hit for key=%s (job_id=%s, status=%s)",
                idempotency_key,
                existing.id,
                existing.status,
            )
            return existing

    # 2. Instantiate new durable job
    now = datetime.now(UTC)
    job = Job(
        id=str(uuid.uuid4()),
        project_id=project_id,
        run_id=run_id,
        job_type=type_str,
        status=JobStatus.QUEUED.value,
        priority=priority,
        payload=payload,
        attempt_number=0,
        max_attempts=eff_max_attempts,
        retry_delay_seconds=eff_retry_delay,
        timeout_seconds=eff_timeout,
        queued_at=now,
        idempotency_key=idempotency_key,
    )
    # 3. If tied to a run:
    # - Scan job advances the run lifecycle status
    # - Analysis job updates analysis_outcome independently
    if run_id:
        run = db.query(Run).filter(Run.id == run_id).first()
        if run:
            if type_str == JobType.SCAN.value:
                if run.lifecycle_status in (LifecycleStatus.RECEIVED.value, LifecycleStatus.FAILED.value):
                    run.lifecycle_status = transition_lifecycle(
                        LifecycleStatus(run.lifecycle_status), LifecycleStatus.QUEUED
                    ).value
                run.queued_at = now
            elif type_str == JobType.ANALYSIS.value:
                run.analysis_outcome = AnalysisOutcome.QUEUED.value

    if idempotency_key:
        try:
            with db.begin_nested():
                db.add(job)
                db.flush()
        except IntegrityError:
            # Savepoint rollback isolated the duplicate job conflict; outer transaction work is preserved!
            existing = db.query(Job).filter(Job.idempotency_key == idempotency_key).first()
            if existing:
                logger.info(
                    "Recovered from concurrent duplicate submission for idempotency_key=%s via savepoint",
                    idempotency_key,
                )
                return existing
            raise
    else:
        db.add(job)
        db.flush()

    return job


def claim_next_job(
    db: Session,
    worker_id: str,
    job_types: list[str] | None = None,
) -> Job | None:
    """Atomically discovers and claims the highest-priority eligible queued job.

    Concurrency invariants:
    - Uses SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL to prevent contention.
    - Uses atomic conditional UPDATE on SQLite/other backends.
    - Closes transaction immediately upon claim so execution runs OUTSIDE DB transactions.
    """
    now = datetime.now(UTC)
    bind = db.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    # Base query for eligible queued jobs
    stmt = (
        select(Job)
        .where(
            Job.status == JobStatus.QUEUED.value,
            (Job.next_attempt_at.is_(None)) | (Job.next_attempt_at <= now),
        )
        .order_by(Job.priority.desc(), Job.queued_at.asc())
    )

    if job_types:
        stmt = stmt.where(Job.job_type.in_(job_types))

    if is_postgres:
        # PostgreSQL supports SKIP LOCKED for perfectly serialized concurrent claiming
        stmt = stmt.with_for_update(skip_locked=True).limit(1)
        job = db.scalars(stmt).first()
        if not job:
            return None

        # Transition and claim
        job.status = transition_job_status(JobStatus(job.status), JobStatus.RUNNING).value
        job.locked_by = worker_id
        job.locked_at = now
        job.started_at = job.started_at or now
        job.attempt_number += 1

        if job.run_id:
            run = db.query(Run).filter(Run.id == job.run_id).first()
            if run:
                if job.job_type == JobType.SCAN.value:
                    if run.lifecycle_status == LifecycleStatus.QUEUED.value:
                        run.lifecycle_status = transition_lifecycle(
                            LifecycleStatus(run.lifecycle_status), LifecycleStatus.RUNNING
                        ).value
                    run.started_at = run.started_at or now
                    run.attempt_number = job.attempt_number
                elif job.job_type == JobType.ANALYSIS.value:
                    run.analysis_outcome = AnalysisOutcome.RUNNING.value

        db.commit()
        db.refresh(job)
        return job

    else:
        # SQLite / Non-PostgreSQL atomic claim via conditional update loop
        candidates = db.scalars(stmt.limit(5)).all()
        for candidate in candidates:
            # Atomic compare-and-swap
            update_stmt = (
                update(Job)
                .where(Job.id == candidate.id, Job.status == JobStatus.QUEUED.value)
                .values(
                    status=JobStatus.RUNNING.value,
                    locked_by=worker_id,
                    locked_at=now,
                    started_at=candidate.started_at or now,
                    attempt_number=candidate.attempt_number + 1,
                )
            )
            result = db.execute(update_stmt)
            if result.rowcount == 1:
                # Successfully claimed!
                db.flush()
                if candidate.run_id:
                    run = db.query(Run).filter(Run.id == candidate.run_id).first()
                    if run:
                        if candidate.job_type == JobType.SCAN.value:
                            if run.lifecycle_status == LifecycleStatus.QUEUED.value:
                                run.lifecycle_status = transition_lifecycle(
                                    LifecycleStatus(run.lifecycle_status), LifecycleStatus.RUNNING
                                ).value
                            run.started_at = run.started_at or now
                            run.attempt_number = candidate.attempt_number + 1
                        elif candidate.job_type == JobType.ANALYSIS.value:
                            run.analysis_outcome = AnalysisOutcome.RUNNING.value

                db.commit()
                db.refresh(candidate)
                return candidate

        return None


def complete_job(
    db: Session,
    job_id: str,
    result: dict[str, Any] | None = None,
) -> Job:
    """Record successful execution of a job and update state."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    current_status = JobStatus(job.status)
    if current_status in (JobStatus.CANCELLED, JobStatus.TIMED_OUT, JobStatus.FAILED):
        logger.warning(
            "Job %s was already in terminal state '%s'; ignoring complete_job",
            job.id,
            job.status,
        )
        return job

    now = datetime.now(UTC)
    job.status = transition_job_status(current_status, JobStatus.SUCCEEDED).value
    job.completed_at = now
    job.locked_by = None
    job.locked_at = None
    job.result = result

    if job.run_id:
        run = db.query(Run).filter(Run.id == job.run_id).first()
        if run:
            if job.job_type == JobType.SCAN.value:
                if run.lifecycle_status == LifecycleStatus.RUNNING.value:
                    run.lifecycle_status = transition_lifecycle(
                        LifecycleStatus(run.lifecycle_status), LifecycleStatus.COMPLETED
                    ).value
                run.completed_at = now
            elif job.job_type == JobType.ANALYSIS.value:
                run.analysis_outcome = AnalysisOutcome.SUCCEEDED.value

    db.commit()
    db.refresh(job)
    return job


def fail_job(
    db: Session,
    job_id: str,
    error_message: str,
    failure_code: str | None = None,
    is_transient: bool = False,
) -> Job:
    """Record job failure with bounded exponential-backoff retries.

    If is_transient is True and attempt_number < max_attempts, the job is
    re-queued with backoff delay. Otherwise, the failure is permanent.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    current_status = JobStatus(job.status)
    if current_status in (JobStatus.CANCELLED, JobStatus.TIMED_OUT, JobStatus.SUCCEEDED):
        logger.warning(
            "Job %s was already in terminal state '%s'; ignoring fail_job",
            job.id,
            job.status,
        )
        return job

    now = datetime.now(UTC)
    should_retry = is_transient and (job.attempt_number < job.max_attempts)

    if should_retry:
        # Exponential backoff: base_delay * 2^(attempt - 1)
        backoff_seconds = job.retry_delay_seconds * (2 ** max(0, job.attempt_number - 1))
        job.next_attempt_at = now + timedelta(seconds=backoff_seconds)
        job.status = transition_job_status(current_status, JobStatus.QUEUED).value
        job.locked_by = None
        job.locked_at = None
        job.error_message = error_message
        job.failure_code = failure_code or "transient_failure"

        if job.run_id:
            run = db.query(Run).filter(Run.id == job.run_id).first()
            if run and job.job_type == JobType.SCAN.value:
                run.lifecycle_status = transition_lifecycle(
                    LifecycleStatus(run.lifecycle_status), LifecycleStatus.QUEUED
                ).value

        logger.info(
            "Job %s scheduled for retry attempt %d in %ds (error: %s)",
            job.id,
            job.attempt_number + 1,
            backoff_seconds,
            failure_code,
        )
    else:
        # Permanent failure
        job.status = transition_job_status(current_status, JobStatus.FAILED).value
        job.completed_at = now
        job.locked_by = None
        job.locked_at = None
        job.error_message = error_message
        job.failure_code = failure_code or "execution_failed"

        if job.run_id:
            run = db.query(Run).filter(Run.id == job.run_id).first()
            if run:
                if job.job_type == JobType.SCAN.value:
                    if run.lifecycle_status in (LifecycleStatus.RUNNING.value, LifecycleStatus.QUEUED.value):
                        run.lifecycle_status = transition_lifecycle(
                            LifecycleStatus(run.lifecycle_status), LifecycleStatus.FAILED
                        ).value
                    run.failure_code = job.failure_code
                    run.completed_at = now
                    if run.test_outcome == TestOutcome.PENDING.value:
                        run.test_outcome = TestOutcome.ERROR.value
                elif job.job_type == JobType.ANALYSIS.value:
                    run.analysis_outcome = AnalysisOutcome.UNAVAILABLE.value

        logger.warning(
            "Job %s permanently failed after %d/%d attempts: %s",
            job.id,
            job.attempt_number,
            job.max_attempts,
            error_message,
        )

    db.commit()
    db.refresh(job)
    return job


def timeout_job(
    db: Session,
    job_id: str,
    error_message: str = "Execution exceeded time limit",
) -> Job:
    """Mark a job as timed out."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    current_status = JobStatus(job.status)
    if current_status in (JobStatus.CANCELLED, JobStatus.SUCCEEDED, JobStatus.FAILED):
        logger.warning(
            "Job %s was already in terminal state '%s'; ignoring timeout_job",
            job.id,
            job.status,
        )
        return job

    now = datetime.now(UTC)
    job.status = transition_job_status(current_status, JobStatus.TIMED_OUT).value
    job.completed_at = now
    job.locked_by = None
    job.locked_at = None
    job.failure_code = "job_timed_out"
    job.error_message = error_message

    if job.run_id:
        run = db.query(Run).filter(Run.id == job.run_id).first()
        if run:
            if job.job_type == JobType.SCAN.value:
                if run.lifecycle_status in (LifecycleStatus.RUNNING.value, LifecycleStatus.QUEUED.value):
                    run.lifecycle_status = transition_lifecycle(
                        LifecycleStatus(run.lifecycle_status), LifecycleStatus.TIMED_OUT
                    ).value
                run.failure_code = "job_timed_out"
                run.completed_at = now
                if run.test_outcome == TestOutcome.PENDING.value:
                    run.test_outcome = TestOutcome.TIMED_OUT.value
            elif job.job_type == JobType.ANALYSIS.value:
                run.analysis_outcome = AnalysisOutcome.UNAVAILABLE.value

    db.commit()
    db.refresh(job)
    return job


def cancel_job(
    db: Session,
    job_id: str,
) -> Job:
    """Cancel a queued or running job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise ValueError(f"Job {job_id} not found")

    now = datetime.now(UTC)
    job.status = transition_job_status(JobStatus(job.status), JobStatus.CANCELLED).value
    job.completed_at = now
    job.locked_by = None
    job.locked_at = None

    if job.run_id:
        run = db.query(Run).filter(Run.id == job.run_id).first()
        if run:
            if job.job_type == JobType.SCAN.value:
                if run.lifecycle_status in (LifecycleStatus.RUNNING.value, LifecycleStatus.QUEUED.value):
                    run.lifecycle_status = transition_lifecycle(
                        LifecycleStatus(run.lifecycle_status), LifecycleStatus.CANCELLED
                    ).value
                run.completed_at = now
            elif job.job_type == JobType.ANALYSIS.value:
                run.analysis_outcome = AnalysisOutcome.UNAVAILABLE.value

    db.commit()
    db.refresh(job)
    return job


def recover_interrupted_jobs(
    db: Session,
    timeout_buffer_seconds: int = 30,
) -> int:
    """Recover jobs abandoned by crashed or terminated workers.

    Detects running jobs where locked_at is older than timeout + buffer.
    Re-queues them for another attempt if attempts remain, or fails them cleanly.
    """
    now = datetime.now(UTC)
    running_jobs = db.query(Job).filter(Job.status == JobStatus.RUNNING.value).all()

    recovered_count = 0
    for job in running_jobs:
        locked_at_utc = _as_utc(job.locked_at)
        if not locked_at_utc:
            continue

        lease_cutoff = locked_at_utc + timedelta(
            seconds=(job.timeout_seconds + timeout_buffer_seconds)
        )
        if now > lease_cutoff:
            recovered_count += 1
            if job.attempt_number < job.max_attempts:
                logger.warning(
                    "Recovering abandoned job %s (attempt %d/%d). Resetting to QUEUED.",
                    job.id,
                    job.attempt_number,
                    job.max_attempts,
                )
                job.status = transition_job_status(JobStatus(job.status), JobStatus.QUEUED).value
                job.locked_by = None
                job.locked_at = None
                job.error_message = (
                    f"Recovered from dead worker lease after exceeding {job.timeout_seconds}s limit"
                )
                if job.run_id:
                    run = db.query(Run).filter(Run.id == job.run_id).first()
                    if run:
                        if job.job_type == JobType.SCAN.value:
                            if run.lifecycle_status in (LifecycleStatus.RUNNING.value, LifecycleStatus.QUEUED.value):
                                run.lifecycle_status = transition_lifecycle(
                                    LifecycleStatus(run.lifecycle_status), LifecycleStatus.QUEUED
                                ).value
                        elif job.job_type == JobType.ANALYSIS.value:
                            run.analysis_outcome = AnalysisOutcome.QUEUED.value
            else:
                logger.error(
                    "Job %s exhausted attempts after worker disappearance. Marking FAILED.",
                    job.id,
                )
                job.status = transition_job_status(JobStatus(job.status), JobStatus.FAILED).value
                job.completed_at = now
                job.locked_by = None
                job.locked_at = None
                job.failure_code = "worker_disappeared"
                job.error_message = "Worker lease expired and retry limits exhausted"
                if job.run_id:
                    run = db.query(Run).filter(Run.id == job.run_id).first()
                    if run:
                        if job.job_type == JobType.SCAN.value:
                            if run.lifecycle_status in (LifecycleStatus.RUNNING.value, LifecycleStatus.QUEUED.value):
                                run.lifecycle_status = transition_lifecycle(
                                    LifecycleStatus(run.lifecycle_status), LifecycleStatus.FAILED
                                ).value
                            run.failure_code = "worker_disappeared"
                            run.completed_at = now
                            if run.test_outcome == TestOutcome.PENDING.value:
                                run.test_outcome = TestOutcome.ERROR.value
                        elif job.job_type == JobType.ANALYSIS.value:
                            run.analysis_outcome = AnalysisOutcome.UNAVAILABLE.value

    if recovered_count > 0:
        db.commit()

    return recovered_count
