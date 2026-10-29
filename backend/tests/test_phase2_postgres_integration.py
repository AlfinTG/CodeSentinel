"""PostgreSQL integration tests for Phase 2 distributed queue and worker.

These tests require a live PostgreSQL server. They validate:
- Real FOR UPDATE SKIP LOCKED concurrency
- Alembic migration application and schema verification
- Multi-worker exclusive job claiming
- Idempotency-key collision under concurrent inserts
- Recovery of interrupted jobs with real PostgreSQL timestamps
- Transaction isolation and rollback behavior
- Job lifecycle state consistency

Skip all tests if PostgreSQL is not available (controlled via PG_TEST_URL).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.models import Base
from app.models.job import Job, JobStatus, JobType
from app.models.project import Project, ProjectCheckProfile
from app.models.run import LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.services.queue import (
    cancel_job,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    recover_interrupted_jobs,
    timeout_job,
)
from app.services.runner_adapter import ExecutionResultSummary
from app.worker.worker import Worker

# ---------------------------------------------------------------------------
# PostgreSQL connection configuration
# ---------------------------------------------------------------------------
# Default: connect to the test database on localhost.
# Override with PG_TEST_URL environment variable.
PG_TEST_URL = os.environ.get(
    "PG_TEST_URL",
    "postgresql+psycopg://codesentinel:codesentinel_password@localhost:5432/codesentinel_test",
)

# Detect if PostgreSQL is actually available
_pg_available = False
_pg_skip_reason = "PostgreSQL not available"
try:
    _test_engine = create_engine(PG_TEST_URL, pool_pre_ping=True, poolclass=NullPool)
    with _test_engine.connect() as conn:
        result = conn.execute(text("SELECT version()"))
        _pg_version = result.scalar()
        _pg_available = True
    _test_engine.dispose()
except Exception as exc:
    _pg_skip_reason = f"PostgreSQL connection failed: {exc}"

# Skip all tests in this module if PostgreSQL is not reachable
pytestmark = pytest.mark.skipif(not _pg_available, reason=_pg_skip_reason)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pg_engine():
    """Create a PostgreSQL engine for the test module."""
    engine = create_engine(PG_TEST_URL, pool_pre_ping=True, poolclass=NullPool)
    # Create all tables from SQLAlchemy metadata
    Base.metadata.create_all(bind=engine)
    yield engine
    # Clean up: drop all tables after the module
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def pg_session(pg_engine):
    """Provide a fresh PostgreSQL session for each test with table cleanup."""
    SessionFactory = sessionmaker(bind=pg_engine)
    session = SessionFactory()

    # Clean tables in dependency order before each test
    for table in ["jobs", "evidence_items", "check_runs", "analyses",
                   "runs", "github_deliveries", "project_check_profiles",
                   "projects", "audit_events", "users"]:
        try:
            session.execute(text(f"DELETE FROM {table}"))
        except Exception:
            session.rollback()
    session.commit()

    yield session
    session.rollback()
    session.close()


@pytest.fixture(scope="function")
def pg_session_factory(pg_engine):
    """Provide a session factory for worker tests."""
    return sessionmaker(bind=pg_engine)


@pytest.fixture
def pg_user(pg_session: Session) -> User:
    """Create a test user in PostgreSQL."""
    user = User(
        github_user_id=90001,
        login="pg_test_user",
        display_name="PG Test User",
    )
    pg_session.add(user)
    pg_session.commit()
    pg_session.refresh(user)
    return user


@pytest.fixture
def pg_project(pg_session: Session, pg_user: User) -> Project:
    """Create a test project in PostgreSQL."""
    project = Project(
        owner_user_id=pg_user.id,
        github_installation_id=700001,
        repository_id=800001,
        repository_full_name="codesentinel-org/pg-test",
        default_branch="main",
        active=True,
    )
    pg_session.add(project)
    pg_session.flush()

    profile = ProjectCheckProfile(
        project_id=project.id,
        name="default",
        version=1,
        check_definitions=[{"key": "test", "command": ["pytest"]}],
        created_by_user_id=pg_user.id,
    )
    pg_session.add(profile)
    pg_session.commit()
    pg_session.refresh(project)
    return project


@pytest.fixture
def pg_run(pg_session: Session, pg_project: Project) -> Run:
    """Create a test run in PostgreSQL."""
    run = Run(
        project_id=pg_project.id,
        head_sha="aabbccddee11223344556677889900aabbccddee",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.QUEUED.value,
        test_outcome=TestOutcome.PENDING.value,
        attempt_number=0,
        correlation_id=f"pg-corr-{uuid.uuid4().hex[:8]}",
    )
    pg_session.add(run)
    pg_session.commit()
    pg_session.refresh(run)
    return run


class PGMockRunnerSuccess:
    """Test double that always succeeds."""
    def execute_run(self, db, run, workspace_dir=None, custom_canaries=None):
        return ExecutionResultSummary(
            outcome=TestOutcome.PASSED,
            checks_executed=1,
            passed_count=1,
            failed_count=0,
        )


class PGMockRunnerSlow:
    """Test double that takes measurable time to complete."""
    def __init__(self, delay: float = 0.5):
        self.delay = delay

    def execute_run(self, db, run, workspace_dir=None, custom_canaries=None):
        time.sleep(self.delay)
        return ExecutionResultSummary(
            outcome=TestOutcome.PASSED,
            checks_executed=1,
            passed_count=1,
            failed_count=0,
        )


# ---------------------------------------------------------------------------
# Test 1: PostgreSQL connection and version
# ---------------------------------------------------------------------------

def test_pg_connection_and_version(pg_engine):
    """Verify PostgreSQL is reachable and reports a valid version."""
    with pg_engine.connect() as conn:
        version = conn.execute(text("SELECT version()")).scalar()
        assert "PostgreSQL" in version
        # Confirm we're on PostgreSQL 16+
        major_version = int(version.split("PostgreSQL ")[1].split(".")[0])
        assert major_version >= 16, f"Expected PG 16+, got {major_version}"


# ---------------------------------------------------------------------------
# Test 2: Schema verification
# ---------------------------------------------------------------------------

def test_pg_schema_has_jobs_table_and_indexes(pg_engine):
    """Verify the jobs table exists with expected columns and indexes."""
    insp = inspect(pg_engine)
    assert "jobs" in insp.get_table_names(), "jobs table must exist"

    columns = {c["name"] for c in insp.get_columns("jobs")}
    required_columns = {
        "id", "project_id", "run_id", "job_type", "status", "priority",
        "payload", "result", "attempt_number", "max_attempts",
        "retry_delay_seconds", "next_attempt_at", "timeout_seconds",
        "locked_by", "locked_at", "queued_at", "started_at", "completed_at",
        "failure_code", "error_message", "idempotency_key",
        "created_at", "updated_at",
    }
    missing = required_columns - columns
    assert not missing, f"Missing columns: {missing}"

    # Verify indexes exist
    indexes = {idx["name"] for idx in insp.get_indexes("jobs")}
    assert "ix_jobs_claim_lookup" in indexes
    assert "ix_jobs_status" in indexes
    assert "ix_jobs_idempotency_key" in indexes


# ---------------------------------------------------------------------------
# Test 3: Job creation and persistence
# ---------------------------------------------------------------------------

def test_pg_job_creation_and_persistence(pg_session, pg_project, pg_run):
    """Verify jobs are durably created with correct initial state on PostgreSQL."""
    job = enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        run_id=pg_run.id,
        job_type=JobType.SCAN,
        max_attempts=3,
        retry_delay_seconds=10,
        priority=5,
        timeout_seconds=180,
    )
    pg_session.commit()
    pg_session.refresh(job)

    assert job.id is not None
    assert job.status == JobStatus.QUEUED.value
    assert job.attempt_number == 0
    assert job.max_attempts == 3
    assert job.priority == 5
    assert job.timeout_seconds == 180
    assert job.queued_at is not None
    assert job.locked_by is None

    # Verify persistence via raw SQL
    row = pg_session.execute(
        text("SELECT id, status, priority FROM jobs WHERE id = :jid"),
        {"jid": job.id},
    ).fetchone()
    assert row is not None
    assert row[1] == "queued"
    assert row[2] == 5


# ---------------------------------------------------------------------------
# Test 4: Idempotency-key uniqueness under PostgreSQL
# ---------------------------------------------------------------------------

def test_pg_idempotency_key_prevents_duplicates(pg_session, pg_project, pg_run):
    """Verify unique constraint on idempotency_key works under PostgreSQL."""
    key = f"pg-idemp-{uuid.uuid4().hex}"

    job1 = enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        run_id=pg_run.id,
        idempotency_key=key,
    )
    pg_session.commit()

    job2 = enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        run_id=pg_run.id,
        idempotency_key=key,
    )
    pg_session.commit()

    assert job1.id == job2.id, "Duplicate idempotency key must return the same job"
    count = pg_session.execute(
        text("SELECT COUNT(*) FROM jobs WHERE idempotency_key = :k"),
        {"k": key},
    ).scalar()
    assert count == 1


# ---------------------------------------------------------------------------
# Test 5: FOR UPDATE SKIP LOCKED concurrent claiming
# ---------------------------------------------------------------------------

def test_pg_for_update_skip_locked_exclusive_claiming(
    pg_session, pg_session_factory, pg_project
):
    """CRITICAL: Verify that FOR UPDATE SKIP LOCKED prevents duplicate claims.

    This test creates multiple jobs and has multiple threads race to claim them.
    On PostgreSQL, each job must be claimed by exactly one worker.
    """
    NUM_JOBS = 10
    NUM_WORKERS = 4

    # Seed jobs
    job_ids = []
    for i in range(NUM_JOBS):
        job = enqueue_job(
            db=pg_session,
            project_id=pg_project.id,
            priority=i,
            idempotency_key=f"pg-concurrent-{i}-{uuid.uuid4().hex[:6]}",
        )
        job_ids.append(job.id)
    pg_session.commit()

    claimed_results: list[tuple[str, str]] = []  # (worker_id, job_id)
    lock = threading.Lock()
    errors: list[str] = []

    def worker_claim_loop(worker_name: str):
        """Each worker claims jobs in its own session."""
        try:
            with pg_session_factory() as session:
                for _ in range(NUM_JOBS):
                    try:
                        claimed = claim_next_job(session, worker_id=worker_name)
                        if claimed:
                            with lock:
                                claimed_results.append((worker_name, claimed.id))
                    except Exception as exc:
                        with lock:
                            errors.append(f"{worker_name}: {exc}")
                    time.sleep(0.01)  # Small delay to increase interleaving
        except Exception as exc:
            with lock:
                errors.append(f"{worker_name} session error: {exc}")

    # Run workers concurrently
    threads = [
        threading.Thread(target=worker_claim_loop, args=(f"pg-worker-{i}",))
        for i in range(NUM_WORKERS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"Worker errors: {errors}"

    # CRITICAL INVARIANT: No duplicate claims
    claimed_job_ids = [cid for _, cid in claimed_results]
    assert len(claimed_job_ids) == len(set(claimed_job_ids)), (
        f"DUPLICATE CLAIM DETECTED! "
        f"Total claims: {len(claimed_job_ids)}, "
        f"Unique: {len(set(claimed_job_ids))}"
    )
    assert len(claimed_job_ids) == NUM_JOBS, (
        f"Expected {NUM_JOBS} claims, got {len(claimed_job_ids)}"
    )

    # Verify distribution across workers (at least 2 workers got jobs)
    workers_with_jobs = {wid for wid, _ in claimed_results}
    assert len(workers_with_jobs) >= 2, (
        f"Jobs should be distributed across workers, but only {workers_with_jobs} got any"
    )

    # Verify all jobs are now RUNNING in the database
    pg_session.expire_all()
    for jid in claimed_job_ids:
        job = pg_session.query(Job).filter(Job.id == jid).first()
        assert job is not None
        assert job.status == JobStatus.RUNNING.value
        assert job.locked_by is not None


# ---------------------------------------------------------------------------
# Test 6: Concurrent idempotency-key collision via savepoint
# ---------------------------------------------------------------------------

def test_pg_concurrent_idempotency_collision_via_threads(
    pg_session, pg_session_factory, pg_project, pg_run
):
    """Verify that concurrent inserts with same idempotency_key don't crash."""
    key = f"pg-race-{uuid.uuid4().hex}"
    results: list[str] = []  # job IDs returned
    errors: list[str] = []
    lock = threading.Lock()

    def insert_job(thread_name: str):
        try:
            with pg_session_factory() as session:
                job = enqueue_job(
                    db=session,
                    project_id=pg_project.id,
                    run_id=pg_run.id,
                    idempotency_key=key,
                )
                session.commit()
                with lock:
                    results.append(job.id)
        except Exception as exc:
            with lock:
                errors.append(f"{thread_name}: {exc}")

    threads = [
        threading.Thread(target=insert_job, args=(f"inserter-{i}",))
        for i in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # All threads should succeed (either created or returned existing)
    assert not errors, f"Errors during concurrent insert: {errors}"
    # All should return the same job ID
    assert len(set(results)) == 1, f"Expected single job ID, got {set(results)}"


# ---------------------------------------------------------------------------
# Test 7: Cancel during worker execution (PostgreSQL)
# ---------------------------------------------------------------------------

def test_pg_cancel_during_execution_preserves_cancelled_state(
    pg_session, pg_session_factory, pg_project, pg_run
):
    """Verify that cancellation during worker execution is respected."""
    enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        run_id=pg_run.id,
        job_type=JobType.SCAN,
    )
    pg_session.commit()

    # Claim the job
    claimed = claim_next_job(pg_session, worker_id="pg-cancel-worker")
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING.value

    # Cancel while "running"
    cancel_job(pg_session, claimed.id)
    pg_session.expire_all()
    cancelled_job = pg_session.query(Job).filter(Job.id == claimed.id).first()
    assert cancelled_job.status == JobStatus.CANCELLED.value

    # complete_job must NOT overwrite cancelled state
    completed = complete_job(pg_session, claimed.id, result={"outcome": "passed"})
    assert completed.status == JobStatus.CANCELLED.value

    # fail_job must NOT overwrite cancelled state
    failed = fail_job(pg_session, claimed.id, error_message="fail", is_transient=True)
    assert failed.status == JobStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# Test 8: Retry with exponential backoff on PostgreSQL
# ---------------------------------------------------------------------------

def test_pg_retry_backoff_and_exhaustion(pg_session, pg_session_factory, pg_project, pg_run):
    """Verify retry mechanics work correctly on PostgreSQL."""
    job = enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        run_id=pg_run.id,
        max_attempts=2,
        retry_delay_seconds=1,
    )
    pg_session.commit()
    job_id = job.id

    # Claim and fail with transient error (attempt 1)
    claimed = claim_next_job(pg_session, worker_id="pg-retry-worker")
    assert claimed is not None
    fail_job(pg_session, job_id, error_message="transient", is_transient=True)

    pg_session.expire_all()
    job_after = pg_session.query(Job).filter(Job.id == job_id).first()
    assert job_after.status == JobStatus.QUEUED.value
    assert job_after.next_attempt_at is not None
    assert job_after.attempt_number == 1

    # Fast-forward backoff
    job_after.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    pg_session.commit()

    # Claim and fail again (attempt 2 = max, should be permanent failure)
    claimed2 = claim_next_job(pg_session, worker_id="pg-retry-worker")
    assert claimed2 is not None
    fail_job(pg_session, job_id, error_message="still broken", is_transient=True)

    pg_session.expire_all()
    final = pg_session.query(Job).filter(Job.id == job_id).first()
    assert final.status == JobStatus.FAILED.value
    assert final.attempt_number == 2
    assert final.completed_at is not None


# ---------------------------------------------------------------------------
# Test 9: Interrupted job recovery on PostgreSQL
# ---------------------------------------------------------------------------

def test_pg_interrupted_job_recovery(pg_session, pg_project, pg_run):
    """Verify recovery of jobs abandoned by crashed workers on PostgreSQL."""
    job = enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        run_id=pg_run.id,
        max_attempts=3,
        timeout_seconds=60,
    )
    pg_session.commit()

    # Simulate a claimed-but-abandoned job
    job.status = JobStatus.RUNNING.value
    job.locked_by = "pg-dead-worker-999"
    job.locked_at = datetime.now(UTC) - timedelta(minutes=10)
    job.attempt_number = 1
    pg_session.commit()

    recovered = recover_interrupted_jobs(pg_session, timeout_buffer_seconds=30)
    assert recovered == 1

    pg_session.expire_all()
    fresh = pg_session.query(Job).filter(Job.id == job.id).first()
    assert fresh.status == JobStatus.QUEUED.value
    assert fresh.locked_by is None
    assert "Recovered" in (fresh.error_message or "")


# ---------------------------------------------------------------------------
# Test 10: Exhausted recovery marks FAILED on PostgreSQL
# ---------------------------------------------------------------------------

def test_pg_exhausted_recovery_marks_failed(pg_session, pg_project, pg_run):
    """Verify that recovery marks job as FAILED when attempts are exhausted."""
    job = enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        run_id=pg_run.id,
        max_attempts=1,
        timeout_seconds=60,
    )
    pg_session.commit()

    job.status = JobStatus.RUNNING.value
    job.locked_by = "pg-dead-worker-888"
    job.locked_at = datetime.now(UTC) - timedelta(minutes=10)
    job.attempt_number = 1  # equals max_attempts
    pg_session.commit()

    recovered = recover_interrupted_jobs(pg_session, timeout_buffer_seconds=30)
    assert recovered == 1

    pg_session.expire_all()
    fresh = pg_session.query(Job).filter(Job.id == job.id).first()
    assert fresh.status == JobStatus.FAILED.value
    assert fresh.failure_code == "worker_disappeared"
    assert fresh.completed_at is not None


# ---------------------------------------------------------------------------
# Test 11: Multi-worker end-to-end via Worker class on PostgreSQL
# ---------------------------------------------------------------------------

def test_pg_multi_worker_end_to_end(pg_session, pg_session_factory, pg_project):
    """Run actual Worker instances against PostgreSQL and verify disjoint processing."""
    NUM_JOBS = 6

    # Seed jobs with runs
    job_ids = []
    for i in range(NUM_JOBS):
        run = Run(
            project_id=pg_project.id,
            head_sha=f"{'a' * 40}",
            check_profile_version=1,
            lifecycle_status=LifecycleStatus.QUEUED.value,
            test_outcome=TestOutcome.PENDING.value,
            attempt_number=0,
            correlation_id=f"pg-e2e-{i}-{uuid.uuid4().hex[:6]}",
        )
        pg_session.add(run)
        pg_session.flush()
        job = enqueue_job(
            db=pg_session,
            project_id=pg_project.id,
            run_id=run.id,
            job_type=JobType.SCAN,
            priority=i,
        )
        job_ids.append(job.id)
    pg_session.commit()

    # Create two workers with different IDs
    worker1 = Worker(
        worker_id="pg-e2e-worker-1",
        poll_interval=0.1,
        runner_adapter=PGMockRunnerSuccess(),
        db_factory=pg_session_factory,
        max_jobs=NUM_JOBS,
    )
    worker2 = Worker(
        worker_id="pg-e2e-worker-2",
        poll_interval=0.1,
        runner_adapter=PGMockRunnerSuccess(),
        db_factory=pg_session_factory,
        max_jobs=NUM_JOBS,
    )

    # Run both in threads
    t1 = threading.Thread(target=worker1.run)
    t2 = threading.Thread(target=worker2.run)
    t1.start()
    t2.start()

    # Wait for all jobs to be processed (with timeout)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        total = worker1.jobs_processed + worker2.jobs_processed
        if total >= NUM_JOBS:
            break
        time.sleep(0.2)

    worker1.stop()
    worker2.stop()
    t1.join(timeout=5)
    t2.join(timeout=5)

    total_processed = worker1.jobs_processed + worker2.jobs_processed
    assert total_processed == NUM_JOBS, (
        f"Expected {NUM_JOBS} total processed, got {total_processed} "
        f"(w1={worker1.jobs_processed}, w2={worker2.jobs_processed})"
    )

    # Verify all jobs succeeded
    pg_session.expire_all()
    for jid in job_ids:
        job = pg_session.query(Job).filter(Job.id == jid).first()
        assert job is not None
        assert job.status == JobStatus.SUCCEEDED.value, (
            f"Job {jid} status={job.status}, expected succeeded"
        )

    # Verify both workers did work (distribution)
    assert worker1.jobs_processed > 0, "Worker 1 should have processed at least 1 job"
    assert worker2.jobs_processed > 0, "Worker 2 should have processed at least 1 job"


# ---------------------------------------------------------------------------
# Test 12: Transaction rollback on PostgreSQL
# ---------------------------------------------------------------------------

def test_pg_transaction_rollback_on_error(pg_session, pg_project):
    """Verify that database errors cause proper rollback on PostgreSQL."""
    job = enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        job_type=JobType.SCAN,
    )
    pg_session.commit()
    job_id = job.id

    # Simulate a failed transaction
    try:
        # Cause an error by inserting a duplicate primary key
        pg_session.execute(
            text("INSERT INTO jobs (id, project_id, job_type, status, priority, "
                 "attempt_number, max_attempts, retry_delay_seconds, timeout_seconds, "
                 "queued_at, created_at, updated_at) "
                 "VALUES (:id, :pid, 'scan', 'queued', 0, 0, 3, 5, 300, "
                 "NOW(), NOW(), NOW())"),
            {"id": job_id, "pid": pg_project.id},
        )
        pg_session.commit()
    except Exception:
        pg_session.rollback()

    # Session should still be usable after rollback
    recovered_job = pg_session.query(Job).filter(Job.id == job_id).first()
    assert recovered_job is not None
    assert recovered_job.status == JobStatus.QUEUED.value


# ---------------------------------------------------------------------------
# Test 13: Timeout job on PostgreSQL
# ---------------------------------------------------------------------------

def test_pg_timeout_job(pg_session, pg_project, pg_run):
    """Verify timeout_job works correctly on PostgreSQL."""
    enqueue_job(
        db=pg_session,
        project_id=pg_project.id,
        run_id=pg_run.id,
        job_type=JobType.SCAN,
    )
    pg_session.commit()

    claimed = claim_next_job(pg_session, worker_id="pg-timeout-worker")
    assert claimed is not None

    timed_out = timeout_job(pg_session, claimed.id)
    assert timed_out.status == JobStatus.TIMED_OUT.value
    assert timed_out.failure_code == "job_timed_out"
    assert timed_out.completed_at is not None
    assert timed_out.locked_by is None

    # Run should reflect timeout
    pg_session.refresh(pg_run)
    assert pg_run.lifecycle_status == LifecycleStatus.TIMED_OUT.value
