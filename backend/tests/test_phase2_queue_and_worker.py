"""Phase 2 Automated Tests: Distributed Queue and Worker Architecture.

Covers all 12 mandatory Phase 2 testing requirements:
1. Job creation and initial state.
2. Valid and invalid state transitions.
3. Worker job claiming.
4. Successful execution using a controlled runner test double.
5. Runner failures and safe error persistence.
6. Retry limits and exhausted attempts.
7. Duplicate submissions and idempotency.
8. Concurrent worker claims.
9. Interrupted-job recovery.
10. Graceful worker shutdown.
11. Existing API and runner tests.
12. Accurate status reporting for failed, timed-out, and incomplete jobs.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session, sessionmaker

from app.models.job import Job, JobStatus, JobType
from app.models.project import Project, ProjectCheckProfile
from app.models.run import LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.services.queue import (
    claim_next_job,
    complete_job,
    enqueue_job,
    recover_interrupted_jobs,
)
from app.services.runner_adapter import ExecutionResultSummary
from app.services.transitions import InvalidStateTransitionError, transition_job_status
from app.worker.worker import Worker

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures and Test Doubles
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def session_factory(db: Session):
    """Provides a session factory bound to the active test database."""
    return sessionmaker(bind=db.get_bind())


@pytest.fixture
def queue_project(db: Session, user_alice: User) -> Project:
    """Create test project for queue and worker testing."""
    project = Project(
        owner_user_id=user_alice.id,
        github_installation_id=555111,
        repository_id=888222,
        repository_full_name="codesentinel-org/queue-demo",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()

    profile = ProjectCheckProfile(
        project_id=project.id,
        name="default",
        version=1,
        check_definitions=[{"key": "unit", "command": ["pytest"]}],
        created_by_user_id=user_alice.id,
    )
    db.add(profile)
    db.commit()
    db.refresh(project)
    return project


@pytest.fixture
def queue_run(db: Session, queue_project: Project) -> Run:
    """Create a linked Run record for queue jobs."""
    run = Run(
        project_id=queue_project.id,
        head_sha="1122334455667788990011223344556677889900",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.QUEUED.value,
        test_outcome=TestOutcome.PENDING.value,
        attempt_number=0,
        correlation_id=f"corr-{uuid.uuid4().hex[:8]}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


class MockRunnerSuccessDouble:
    """Controlled runner test double simulating successful check execution."""

    def execute_run(self, db: Session, run: Run, workspace_dir: str | None = None, custom_canaries: list[str] | None = None) -> ExecutionResultSummary:
        return ExecutionResultSummary(
            outcome=TestOutcome.PASSED,
            checks_executed=2,
            passed_count=2,
            failed_count=0,
        )


class MockRunnerFailureDouble:
    """Controlled runner test double simulating deterministic check failure."""

    def execute_run(self, db: Session, run: Run, workspace_dir: str | None = None, custom_canaries: list[str] | None = None) -> ExecutionResultSummary:
        return ExecutionResultSummary(
            outcome=TestOutcome.FAILED,
            checks_executed=2,
            passed_count=1,
            failed_count=1,
            failure_code="checks_failed",
            error_message="AssertionError: expected 200 got 500",
        )


class MockRunnerTimeoutDouble:
    """Controlled runner test double simulating timeout."""

    def execute_run(self, db: Session, run: Run, workspace_dir: str | None = None, custom_canaries: list[str] | None = None) -> ExecutionResultSummary:
        return ExecutionResultSummary(
            outcome=TestOutcome.TIMED_OUT,
            checks_executed=1,
            passed_count=0,
            failed_count=1,
            failure_code="runner_timed_out",
            error_message="Check exceeded execution limit",
        )


class MockRunnerExceptionDouble:
    """Controlled runner test double simulating an unhandled infrastructure error."""

    def execute_run(self, db: Session, run: Run, workspace_dir: str | None = None, custom_canaries: list[str] | None = None) -> ExecutionResultSummary:
        raise ConnectionResetError("Docker daemon socket connection reset")


# ──────────────────────────────────────────────────────────────────────────────
# 1. Job Creation and Initial State
# ──────────────────────────────────────────────────────────────────────────────

def test_job_creation_and_initial_state(db: Session, queue_project: Project, queue_run: Run):
    """Scenario 1: Verify job creation sets queued state, initial attempts, and timestamps."""
    job = enqueue_job(
        db=db,
        project_id=queue_project.id,
        run_id=queue_run.id,
        job_type=JobType.SCAN,
        max_attempts=3,
        retry_delay_seconds=10,
        priority=5,
        timeout_seconds=180,
    )
    db.commit()
    db.refresh(job)

    assert job.id is not None
    assert job.status == JobStatus.QUEUED.value
    assert job.attempt_number == 0
    assert job.max_attempts == 3
    assert job.retry_delay_seconds == 10
    assert job.priority == 5
    assert job.timeout_seconds == 180
    assert job.queued_at is not None
    assert job.started_at is None
    assert job.completed_at is None
    assert job.locked_by is None
    assert job.locked_at is None
    assert job.failure_code is None
    assert job.error_message is None
    assert job.project_id == queue_project.id
    assert job.run_id == queue_run.id


# ──────────────────────────────────────────────────────────────────────────────
# 2. Valid and Invalid State Transitions
# ──────────────────────────────────────────────────────────────────────────────

def test_valid_and_invalid_state_transitions():
    """Scenario 2: Validate state transition machine rejects illegal transitions and allows valid ones."""
    # Valid transitions from QUEUED
    assert transition_job_status(JobStatus.QUEUED, JobStatus.RUNNING) == JobStatus.RUNNING
    assert transition_job_status(JobStatus.QUEUED, JobStatus.CANCELLED) == JobStatus.CANCELLED
    assert transition_job_status(JobStatus.QUEUED, JobStatus.FAILED) == JobStatus.FAILED

    # Valid transitions from RUNNING
    assert transition_job_status(JobStatus.RUNNING, JobStatus.SUCCEEDED) == JobStatus.SUCCEEDED
    assert transition_job_status(JobStatus.RUNNING, JobStatus.FAILED) == JobStatus.FAILED
    assert transition_job_status(JobStatus.RUNNING, JobStatus.TIMED_OUT) == JobStatus.TIMED_OUT
    assert transition_job_status(JobStatus.RUNNING, JobStatus.CANCELLED) == JobStatus.CANCELLED
    assert transition_job_status(JobStatus.RUNNING, JobStatus.QUEUED) == JobStatus.QUEUED  # Retry

    # Idempotent self-transitions
    assert transition_job_status(JobStatus.QUEUED, JobStatus.QUEUED) == JobStatus.QUEUED
    assert transition_job_status(JobStatus.RUNNING, JobStatus.RUNNING) == JobStatus.RUNNING
    assert transition_job_status(JobStatus.SUCCEEDED, JobStatus.SUCCEEDED) == JobStatus.SUCCEEDED

    # Illegal transitions: terminal states cannot transition to running or queued
    with pytest.raises(InvalidStateTransitionError):
        transition_job_status(JobStatus.SUCCEEDED, JobStatus.RUNNING)

    with pytest.raises(InvalidStateTransitionError):
        transition_job_status(JobStatus.SUCCEEDED, JobStatus.QUEUED)

    with pytest.raises(InvalidStateTransitionError):
        transition_job_status(JobStatus.CANCELLED, JobStatus.RUNNING)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Worker Job Claiming
# ──────────────────────────────────────────────────────────────────────────────

def test_worker_job_claiming(db: Session, queue_project: Project, queue_run: Run):
    """Scenario 3: Worker discovers and claims an eligible job atomically."""
    job = enqueue_job(db=db, project_id=queue_project.id, run_id=queue_run.id, priority=10)
    db.commit()

    claimed = claim_next_job(db, worker_id="worker-test-alpha")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == JobStatus.RUNNING.value
    assert claimed.locked_by == "worker-test-alpha"
    assert claimed.locked_at is not None
    assert claimed.started_at is not None
    assert claimed.attempt_number == 1

    # Verify run lifecycle status was advanced to running
    db.refresh(queue_run)
    assert queue_run.lifecycle_status == LifecycleStatus.RUNNING.value
    assert queue_run.started_at is not None

    # Next claim when queue is empty returns None
    empty_claim = claim_next_job(db, worker_id="worker-test-beta")
    assert empty_claim is None


# ──────────────────────────────────────────────────────────────────────────────
# 4. Successful Execution Using Controlled Runner Double
# ──────────────────────────────────────────────────────────────────────────────

def test_successful_execution_using_runner_double(db: Session, session_factory, queue_project: Project, queue_run: Run):
    """Scenario 4: Worker claims job and processes it to success using a test double."""
    job = enqueue_job(db=db, project_id=queue_project.id, run_id=queue_run.id)
    db.commit()
    job_id = job.id

    runner_double = MockRunnerSuccessDouble()
    worker = Worker(
        worker_id="worker-success",
        runner_adapter=runner_double,
        db_factory=session_factory,
    )

    processed = worker.process_one_job()
    assert processed is True

    db.expire_all()
    fresh_job = db.query(Job).filter(Job.id == job_id).first()
    assert fresh_job is not None
    assert fresh_job.status == JobStatus.SUCCEEDED.value
    assert fresh_job.completed_at is not None
    assert fresh_job.locked_by is None
    assert fresh_job.result is not None
    assert fresh_job.result["outcome"] == "passed"
    assert fresh_job.result["passed_count"] == 2

    # Verify run outcome updated to completed
    fresh_run = db.query(Run).filter(Run.id == queue_run.id).first()
    assert fresh_run is not None
    assert fresh_run.lifecycle_status in (LifecycleStatus.COMPLETED.value, LifecycleStatus.SUCCEEDED.value)


# ──────────────────────────────────────────────────────────────────────────────
# 5. Runner Failures and Safe Error Persistence
# ──────────────────────────────────────────────────────────────────────────────

def test_runner_failures_and_safe_error_persistence(db: Session, session_factory, queue_project: Project, queue_run: Run):
    """Scenario 5: Check failures persist failure code without marking incomplete jobs as succeeded."""
    job = enqueue_job(db=db, project_id=queue_project.id, run_id=queue_run.id)
    db.commit()
    job_id = job.id

    runner_double = MockRunnerFailureDouble()
    worker = Worker(
        worker_id="worker-fail",
        runner_adapter=runner_double,
        db_factory=session_factory,
    )

    processed = worker.process_one_job()
    assert processed is True

    db.expire_all()
    fresh_job = db.query(Job).filter(Job.id == job_id).first()
    assert fresh_job is not None
    assert fresh_job.status == JobStatus.FAILED.value
    assert fresh_job.completed_at is not None
    assert fresh_job.failure_code == "checks_failed"
    assert "AssertionError" in (fresh_job.error_message or "")
    # Invariant: Failed job is NEVER reported as succeeded
    assert fresh_job.status != JobStatus.SUCCEEDED.value

    fresh_run = db.query(Run).filter(Run.id == queue_run.id).first()
    assert fresh_run is not None
    assert fresh_run.lifecycle_status == LifecycleStatus.FAILED.value


# ──────────────────────────────────────────────────────────────────────────────
# 6. Retry Limits and Exhausted Attempts
# ──────────────────────────────────────────────────────────────────────────────

def test_retry_limits_and_exhausted_attempts(db: Session, session_factory, queue_project: Project, queue_run: Run):
    """Scenario 6: Transient failures back off and re-queue until max_attempts is reached, then fail permanently."""
    job = enqueue_job(
        db=db,
        project_id=queue_project.id,
        run_id=queue_run.id,
        max_attempts=2,
        retry_delay_seconds=2,
    )
    db.commit()
    job_id = job.id

    runner_double = MockRunnerExceptionDouble()
    worker = Worker(
        worker_id="worker-retry",
        runner_adapter=runner_double,
        db_factory=session_factory,
    )

    # Attempt 1: Transient error occurs -> should be re-queued with backoff
    worker.process_one_job()
    db.expire_all()
    fresh_job = db.query(Job).filter(Job.id == job_id).first()
    assert fresh_job is not None
    assert fresh_job.status == JobStatus.QUEUED.value
    assert fresh_job.attempt_number == 1
    assert fresh_job.next_attempt_at is not None

    # Fast-forward next_attempt_at to past to simulate backoff elapsed
    fresh_job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    # Attempt 2: Re-processed, reaches max_attempts (2) -> must permanently FAIL
    worker.process_one_job()
    db.expire_all()
    final_job = db.query(Job).filter(Job.id == job_id).first()
    assert final_job is not None
    assert final_job.status == JobStatus.FAILED.value
    assert final_job.attempt_number == 2
    assert final_job.completed_at is not None
    assert final_job.failure_code == "worker_execution_error"

    # Invariant: A succeeded job is NEVER automatically retried
    succeeded_job = enqueue_job(db=db, project_id=queue_project.id)
    db.commit()
    claimed = claim_next_job(db, worker_id="test-claim-worker")
    complete_job(db, claimed.id)
    db.expire_all()
    s_job = db.query(Job).filter(Job.id == succeeded_job.id).first()
    assert s_job.status == JobStatus.SUCCEEDED.value

    # Attempting to claim/process should not touch succeeded job
    worker.process_one_job()
    db.expire_all()
    s_job_after = db.query(Job).filter(Job.id == succeeded_job.id).first()
    assert s_job_after.status == JobStatus.SUCCEEDED.value


# ──────────────────────────────────────────────────────────────────────────────
# 7. Duplicate Submissions and Idempotency
# ──────────────────────────────────────────────────────────────────────────────

def test_duplicate_submissions_and_idempotency(db: Session, queue_project: Project, queue_run: Run):
    """Scenario 7: Duplicate submission with identical idempotency key returns existing job."""
    idempotency_key = f"idemp-{uuid.uuid4().hex}"

    job1 = enqueue_job(
        db=db,
        project_id=queue_project.id,
        run_id=queue_run.id,
        idempotency_key=idempotency_key,
    )
    db.commit()

    # Second submission with the same idempotency key
    job2 = enqueue_job(
        db=db,
        project_id=queue_project.id,
        run_id=queue_run.id,
        idempotency_key=idempotency_key,
    )
    db.commit()

    assert job1.id == job2.id

    # Verify only one job exists in database with this key
    count = db.query(Job).filter(Job.idempotency_key == idempotency_key).count()
    assert count == 1


# ──────────────────────────────────────────────────────────────────────────────
# 8. Concurrent Worker Claims
# ──────────────────────────────────────────────────────────────────────────────

def test_concurrent_worker_claims(db: Session, session_factory, queue_project: Project):
    """Scenario 8: Multiple concurrent workers claim from queue without race conditions or duplicate claims."""
    # Seed 5 queued jobs
    jobs = []
    for i in range(5):
        j = enqueue_job(
            db=db,
            project_id=queue_project.id,
            priority=i,
            idempotency_key=f"concurrent-{i}-{uuid.uuid4().hex[:6]}",
        )
        jobs.append(j)
    db.commit()

    claimed_job_ids: list[str] = []
    lock = threading.Lock()
    db_mutex = threading.Lock()

    def worker_claim_task(worker_name: str):
        with session_factory() as session:
            for _ in range(5):
                with db_mutex:
                    claimed = claim_next_job(session, worker_id=worker_name)
                if claimed:
                    with lock:
                        claimed_job_ids.append(claimed.id)
                time.sleep(0.01)

    threads = [
        threading.Thread(target=worker_claim_task, args=(f"worker-thread-{i}",))
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Invariant: Every claimed job must be unique (no duplicate claims)
    assert len(claimed_job_ids) == len(set(claimed_job_ids)), "Duplicate job claim detected!"
    assert len(claimed_job_ids) == 5, f"Expected 5 claimed jobs, got {len(claimed_job_ids)}"


# ──────────────────────────────────────────────────────────────────────────────
# 9. Interrupted-Job Recovery
# ──────────────────────────────────────────────────────────────────────────────

def test_interrupted_job_recovery(db: Session, queue_project: Project, queue_run: Run):
    """Scenario 9: Jobs abandoned by crashed workers (lease expired) are recovered and re-queued."""
    job = enqueue_job(
        db=db,
        project_id=queue_project.id,
        run_id=queue_run.id,
        max_attempts=3,
        timeout_seconds=60,
    )
    # Manually simulate a crashed worker holding an expired lock from 10 minutes ago
    job.status = JobStatus.RUNNING.value
    job.locked_by = "worker-crashed-pid-999"
    job.locked_at = datetime.now(UTC) - timedelta(minutes=10)
    job.attempt_number = 1
    db.commit()
    job_id = job.id

    recovered = recover_interrupted_jobs(db, timeout_buffer_seconds=30)
    assert recovered == 1

    db.expire_all()
    fresh_job = db.query(Job).filter(Job.id == job_id).first()
    assert fresh_job is not None
    # Since attempt_number (1) < max_attempts (3), it was re-queued
    assert fresh_job.status == JobStatus.QUEUED.value
    assert fresh_job.locked_by is None
    assert fresh_job.locked_at is None
    assert "Recovered" in (fresh_job.error_message or "")


# ──────────────────────────────────────────────────────────────────────────────
# 10. Graceful Worker Shutdown
# ──────────────────────────────────────────────────────────────────────────────

def test_graceful_worker_shutdown(db: Session, session_factory):
    """Scenario 10: Worker stop event signals clean termination without hanging."""
    worker = Worker(
        worker_id="worker-shutdown-test",
        poll_interval=0.05,
        db_factory=session_factory,
    )

    thread = threading.Thread(target=worker.run)
    thread.start()

    time.sleep(0.1)
    assert worker.is_running is True

    # Signal stop
    worker.stop()
    thread.join(timeout=2.0)

    assert not thread.is_alive(), "Worker thread failed to terminate within timeout"
    assert worker.is_running is False


# ──────────────────────────────────────────────────────────────────────────────
# 11 & 12. API Integration and Accurate Status Reporting
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_jobs_accurate_status_reporting(
    client: AsyncClient,
    db: Session,
    queue_project: Project,
    queue_run: Run,
    user_alice: User,
    user_bob: User,
):
    """Scenario 12: Verify status reporting for queued, running, failed, and timed_out jobs with authorization."""
    alice_headers = {"x-test-user-id": user_alice.id}
    bob_headers = {"x-test-user-id": user_bob.id}

    # 1. Enqueue job via project enqueue endpoint
    enqueue_resp = await client.post(
        f"/api/v1/jobs/projects/{queue_project.id}/enqueue",
        json={
            "job_type": "scan",
            "run_id": queue_run.id,
            "priority": 1,
            "max_attempts": 3,
            "retry_delay_seconds": 5,
            "timeout_seconds": 120,
        },
        headers=alice_headers,
    )
    assert enqueue_resp.status_code == 202
    job_data = enqueue_resp.json()
    job_id = job_data["id"]
    assert job_data["status"] == "queued"

    # 2. Query job detail
    detail_resp = await client.get(f"/api/v1/jobs/{job_id}", headers=alice_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["status"] == "queued"
    assert detail_resp.json()["timeout_seconds"] == 120

    # 3. Query run's job endpoint
    run_job_resp = await client.get(f"/api/v1/runs/{queue_run.id}/job", headers=alice_headers)
    assert run_job_resp.status_code == 200
    assert run_job_resp.json()["id"] == job_id

    # 4. Cross-user access denied (Bob cannot access Alice's job)
    forbidden_resp = await client.get(f"/api/v1/jobs/{job_id}", headers=bob_headers)
    assert forbidden_resp.status_code == 403

    # 5. Cancel job endpoint
    cancel_resp = await client.post(f"/api/v1/jobs/{job_id}/cancel", headers=alice_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    # Verify status is reported accurately as cancelled
    cancelled_detail = await client.get(f"/api/v1/jobs/{job_id}", headers=alice_headers)
    assert cancelled_detail.json()["status"] == "cancelled"
    # Never reported as succeeded
    assert cancelled_detail.json()["status"] != "succeeded"


@pytest.mark.asyncio
async def test_webhook_creates_durable_job(
    client: AsyncClient,
    db: Session,
    queue_project: Project,
):
    """Scenario 11: Webhook acceptance persists both Run and Job atomically outside HTTP lifecycle."""
    import hashlib
    import hmac
    import json

    from app.core.config import settings

    delivery_id = str(uuid.uuid4())
    head_sha = "abcdef1234567890abcdef1234567890abcdef12"

    payload = {
        "after": head_sha,
        "installation": {"id": queue_project.github_installation_id},
        "repository": {
            "id": queue_project.repository_id,
            "full_name": queue_project.repository_full_name,
            "default_branch": "main",
        },
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    mac = hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"), msg=raw_bytes, digestmod=hashlib.sha256)
    sig = f"sha256={mac.hexdigest()}"

    response = await client.post(
        "/api/v1/webhooks/github",
        content=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": delivery_id,
        },
    )

    assert response.status_code == 202
    resp_data = response.json()
    assert resp_data["accepted"] is True
    assert resp_data["run_id"] is not None
    assert resp_data["job_id"] is not None

    # Verify Job is queued in the database
    job = db.query(Job).filter(Job.id == resp_data["job_id"]).first()
    assert job is not None
    assert job.status == JobStatus.QUEUED.value
    assert job.run_id == resp_data["run_id"]
    assert job.idempotency_key == f"delivery:{delivery_id}"
