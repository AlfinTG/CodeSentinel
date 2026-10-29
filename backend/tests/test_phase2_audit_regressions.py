"""Regression test suite for verified defects and edge cases discovered during Phase 2 audit."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.analysis import Analysis
from app.models.job import Job, JobStatus, JobType
from app.models.project import Project, ProjectCheckProfile
from app.models.run import AnalysisOutcome, LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.services.queue import (
    cancel_job,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
    recover_interrupted_jobs,
)
from app.worker.worker import Worker


@pytest.fixture
def session_factory(db: Session):
    return sessionmaker(bind=db.get_bind())


@pytest.fixture
def audit_project(db: Session, user_alice: User) -> Project:
    project = Project(
        owner_user_id=user_alice.id,
        github_installation_id=999111,
        repository_id=777222,
        repository_full_name="codesentinel-org/audit-demo",
        default_branch="main",
        active=True,
    )
    db.add(project)
    db.flush()

    profile = ProjectCheckProfile(
        project_id=project.id,
        name="default",
        version=1,
        check_definitions=[{"key": "test", "command": ["pytest"]}],
        created_by_user_id=user_alice.id,
    )
    db.add(profile)
    db.commit()
    db.refresh(project)
    return project


@pytest.fixture
def audit_run(db: Session, audit_project: Project) -> Run:
    run = Run(
        project_id=audit_project.id,
        head_sha="abcdef1234567890abcdef1234567890abcdef12",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.QUEUED.value,
        test_outcome=TestOutcome.PENDING.value,
        analysis_outcome=AnalysisOutcome.NOT_REQUESTED.value,
        attempt_number=0,
        correlation_id=f"corr-{uuid.uuid4().hex[:8]}",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_job_cancelled_during_worker_execution_does_not_crash_or_overwrite_status(
    db: Session, audit_project: Project, audit_run: Run, session_factory
):
    """Defect 1: If a running job is cancelled while worker executes, complete_job/fail_job

    must not crash with InvalidStateTransitionError and must not overwrite CANCELLED status.
    """
    enqueue_job(
        db,
        project_id=audit_project.id,
        run_id=audit_run.id,
        job_type=JobType.SCAN,
    )
    db.commit()

    # Claim job
    claimed = claim_next_job(db, worker_id="worker-audit-1")
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING.value

    # User cancels job while worker is executing
    cancel_job(db, claimed.id)
    db.expire_all()

    cancelled_job = db.query(Job).filter(Job.id == claimed.id).first()
    assert cancelled_job.status == JobStatus.CANCELLED.value

    # Worker finishes execution and attempts to complete the job
    completed = complete_job(
        db,
        claimed.id,
        result={"outcome": "passed"},
    )
    assert completed.status == JobStatus.CANCELLED.value, "Cancellation must not be overwritten by complete_job"

    # Worker attempting to fail a cancelled job must also safely preserve cancelled state
    failed = fail_job(
        db,
        claimed.id,
        error_message="Something failed",
        is_transient=True,
    )
    assert failed.status == JobStatus.CANCELLED.value, "Cancellation must not be overwritten by fail_job"


def test_analysis_job_failure_does_not_report_succeeded(
    db: Session, audit_project: Project, audit_run: Run, session_factory
):
    """Defect 2: Failed analysis execution must mark the queue job as FAILED, not SUCCEEDED."""
    audit_run.lifecycle_status = LifecycleStatus.COMPLETED.value
    audit_run.test_outcome = TestOutcome.FAILED.value
    db.commit()

    analysis = Analysis(
        run_id=audit_run.id,
        attempt_number=1,
        provider="nebius_token_factory",
        model_id="test-model",
        status="pending",
        schema_version="1.0",
        input_digest="hash",
    )
    db.add(analysis)
    db.commit()

    job = enqueue_job(
        db,
        project_id=audit_project.id,
        run_id=audit_run.id,
        job_type=JobType.ANALYSIS,
        payload={"analysis_id": analysis.id, "attempt": 1},
    )
    db.commit()

    # Simulate run_analysis marking analysis as failed
    def mock_run_analysis(run, an, session):
        an.status = "failed"
        an.safe_error_code = "provider_unavailable"
        session.commit()

    with patch("app.services.analysis.run_analysis", side_effect=mock_run_analysis):
        worker = Worker(
            worker_id="worker-audit-analysis",
            db_factory=session_factory,
        )
        processed = worker.process_one_job()
        assert processed is True

    db.expire_all()
    job_after = db.query(Job).filter(Job.id == job.id).first()
    assert job_after.status == JobStatus.FAILED.value, (
        f"Failed analysis must produce FAILED job status, got {job_after.status}"
    )


def test_interrupted_job_recovery_resets_run_state_and_handles_analysis(
    db: Session, audit_project: Project, audit_run: Run
):
    """Defect 3: Interrupted job recovery must reset run lifecycle to QUEUED on transient recovery,

    and update analysis_outcome to UNAVAILABLE if analysis job retries are exhausted.
    """
    # Case A: Scan job with attempts remaining
    scan_job = enqueue_job(
        db,
        project_id=audit_project.id,
        run_id=audit_run.id,
        job_type=JobType.SCAN,
        max_attempts=3,
        timeout_seconds=60,
    )
    db.commit()
    claimed_scan = claim_next_job(db, worker_id="worker-dead-1")
    assert claimed_scan is not None

    db.expire_all()
    run_db = db.query(Run).filter(Run.id == audit_run.id).first()
    assert run_db.lifecycle_status == LifecycleStatus.RUNNING.value

    # Simulate dead worker lease
    claimed_scan.locked_at = datetime.now(UTC) - timedelta(seconds=120)
    db.commit()

    recovered = recover_interrupted_jobs(db, timeout_buffer_seconds=10)
    assert recovered == 1

    db.expire_all()
    scan_after = db.query(Job).filter(Job.id == scan_job.id).first()
    run_after = db.query(Run).filter(Run.id == audit_run.id).first()
    assert scan_after.status == JobStatus.QUEUED.value
    assert run_after.lifecycle_status == LifecycleStatus.QUEUED.value, (
        f"Run lifecycle should be reset to QUEUED upon recovery, got {run_after.lifecycle_status}"
    )

    # Case B: Analysis job with exhausted attempts
    analysis_job = enqueue_job(
        db,
        project_id=audit_project.id,
        run_id=audit_run.id,
        job_type=JobType.ANALYSIS,
        max_attempts=1,
        timeout_seconds=60,
    )
    db.commit()
    claimed_analysis = claim_next_job(
        db, worker_id="worker-dead-2", job_types=[JobType.ANALYSIS.value]
    )
    assert claimed_analysis is not None
    assert claimed_analysis.id == analysis_job.id
    claimed_analysis.attempt_number = 1  # max attempts reached
    claimed_analysis.locked_at = datetime.now(UTC) - timedelta(seconds=120)
    db.commit()

    recovered_b = recover_interrupted_jobs(db, timeout_buffer_seconds=10)
    assert recovered_b >= 1

    db.expire_all()
    analysis_job_after = db.query(Job).filter(Job.id == analysis_job.id).first()
    assert analysis_job_after.status == JobStatus.FAILED.value

    run_after_b = db.query(Run).filter(Run.id == audit_run.id).first()
    assert run_after_b.analysis_outcome == AnalysisOutcome.UNAVAILABLE.value, (
        f"Run analysis_outcome should be UNAVAILABLE when analysis retries exhausted, got {run_after_b.analysis_outcome}"
    )


def test_concurrent_idempotency_key_collision_recovers_gracefully(
    db: Session, audit_project: Project, audit_run: Run
):
    """Defect 5 & Item 3: enqueue_job savepoint isolation ensures recovering from duplicate

    idempotency key collision catches IntegrityError, returns existing job, and preserves
    unrelated staged database changes in the outer session transaction without rollback.
    """
    idemp_key = f"concurrent-test-{uuid.uuid4().hex}"
    job1 = enqueue_job(
        db,
        project_id=audit_project.id,
        run_id=audit_run.id,
        idempotency_key=idemp_key,
    )
    db.commit()

    # Stage an unrelated entity in the active session
    unrelated_run = Run(
        project_id=audit_project.id,
        head_sha="9999999999999999999999999999999999999999",
        check_profile_version=1,
        lifecycle_status=LifecycleStatus.QUEUED.value,
        test_outcome=TestOutcome.PENDING.value,
        attempt_number=0,
        correlation_id=f"corr-unrelated-{uuid.uuid4().hex[:8]}",
    )
    db.add(unrelated_run)

    # Simulate concurrent duplicate submission where the in-memory check passed but flush hits IntegrityError
    # Using real begin_nested savepoint to verify outer transaction is preserved
    real_begin_nested = db.begin_nested

    class SimulatedCollisionSavepoint:
        def __init__(self, sp):
            self.sp = sp
        def __enter__(self):
            return self.sp.__enter__()
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.sp.__exit__(IntegrityError, IntegrityError("duplicate key", params=None, orig=Exception()), None)
            raise IntegrityError("duplicate key", params=None, orig=Exception())

    with patch.object(db, "begin_nested", side_effect=lambda: SimulatedCollisionSavepoint(real_begin_nested())):
        recovered_job = enqueue_job(
            db,
            project_id=audit_project.id,
            run_id=audit_run.id,
            idempotency_key=idemp_key,
        )
        assert recovered_job.id == job1.id

    # Commit outer transaction and verify unrelated work is preserved and persisted
    db.commit()
    db.expire_all()
    persisted_unrelated = db.query(Run).filter(Run.id == unrelated_run.id).first()
    assert persisted_unrelated is not None, "Unrelated staged work must not be discarded when recovering from duplicate idempotency key!"


@pytest.mark.asyncio
async def test_run_cancellation_endpoint(
    db: Session, audit_project: Project, audit_run: Run, user_alice: User, client: AsyncClient
):
    """Defect 7: POST /api/v1/runs/{run_id}/cancel per API_CONTRACTS.md."""
    job = enqueue_job(
        db,
        project_id=audit_project.id,
        run_id=audit_run.id,
        job_type=JobType.SCAN,
    )
    db.commit()

    resp = await client.post(
        f"/api/v1/runs/{audit_run.id}/cancel",
        headers={"x-test-user-id": user_alice.id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["run_id"] == audit_run.id
    assert data["lifecycle_status"] == "cancelled"

    db.expire_all()
    job_after = db.query(Job).filter(Job.id == job.id).first()
    assert job_after.status == JobStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_job_cancel_endpoint_sanitized_error(
    db: Session, audit_project: Project, audit_run: Run, user_alice: User, client: AsyncClient
):
    """Defect 8: POST /api/v1/jobs/{job_id}/cancel returns sanitized error on invalid state."""
    job = enqueue_job(
        db,
        project_id=audit_project.id,
        run_id=audit_run.id,
        job_type=JobType.SCAN,
    )
    db.commit()

    # Cancel once (should succeed)
    r1 = await client.post(
        f"/api/v1/jobs/{job.id}/cancel",
        headers={"x-test-user-id": user_alice.id},
    )
    assert r1.status_code == 200

    # Mark completed
    db.expire_all()
    job_rec = db.query(Job).filter(Job.id == job.id).first()
    job_rec.status = JobStatus.SUCCEEDED.value
    db.commit()

    # Cancel a succeeded job: must return 400 with sanitized message, not internal exception string
    r2 = await client.post(
        f"/api/v1/jobs/{job.id}/cancel",
        headers={"x-test-user-id": user_alice.id},
    )
    assert r2.status_code == 400
    err = r2.json()["error"]
    assert err["code"] == "invalid_transition"
    assert "InvalidStateTransitionError" not in err["message"]
    assert err["message"] == "Job cannot be cancelled in its current state"


def test_worker_run_loop_survives_transient_database_error(session_factory):
    """Defect 4: Worker main loop does not crash on transient DB error."""
    call_count = 0

    def flaky_process():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Database connection dropped temporarily")
        return False

    worker = Worker(
        worker_id="worker-crash-test",
        poll_interval=0.01,
        db_factory=session_factory,
    )
    worker.process_one_job = flaky_process

    # Run worker in background thread, let it run 2 cycles, then stop
    t = threading.Thread(target=worker.run)
    t.start()
    time.sleep(0.05)
    worker.stop()
    t.join(timeout=2.0)

    assert not t.is_alive(), "Worker must stop cleanly"
    assert call_count >= 2, f"Worker must survive first transient error and continue loop; ran {call_count} times"


def test_worker_config_validation_boundaries():
    """Defect 11 & Task 1: Pydantic settings validates worker bounds and restricts QUEUE_BACKEND to genuinely implemented options."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(WORKER_POLL_INTERVAL_SECONDS=-1.0)

    with pytest.raises(ValidationError):
        Settings(WORKER_DEFAULT_MAX_ATTEMPTS=0)

    with pytest.raises(ValidationError):
        Settings(WORKER_DEFAULT_JOB_TIMEOUT_SECONDS=-5)

    # QUEUE_BACKEND validation
    # Supported options succeed
    s_db = Settings(QUEUE_BACKEND="database")
    assert s_db.QUEUE_BACKEND == "database"

    s_bg = Settings(QUEUE_BACKEND="background")
    assert s_bg.QUEUE_BACKEND == "background"

    # Unsupported options (such as redis, celery, sqs) must be rejected with ValidationError
    with pytest.raises(ValidationError):
        Settings(QUEUE_BACKEND="redis")

    with pytest.raises(ValidationError):
        Settings(QUEUE_BACKEND="celery")


@pytest.mark.asyncio
async def test_concurrent_webhook_delivery_recovers_gracefully(
    client: AsyncClient,
    db: Session,
    audit_project: Project,
):
    """Defect 6: Concurrent duplicate webhook delivery recovers gracefully via DB constraint."""
    import hashlib
    import hmac
    import json

    from app.core.config import settings

    delivery_id = str(uuid.uuid4())
    payload = {
        "repository": {
            "id": audit_project.repository_id,
            "full_name": audit_project.repository_full_name,
            "default_branch": "main",
        },
        "after": "1234567890abcdef1234567890abcdef12345678",
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = f"sha256={hmac.new(settings.GITHUB_WEBHOOK_SECRET.encode(), raw_bytes, hashlib.sha256).hexdigest()}"

    headers = {
        "x-github-delivery": delivery_id,
        "x-github-event": "push",
        "x-hub-signature-256": sig,
        "content-type": "application/json",
    }

    # First delivery
    r1 = await client.post("/api/v1/webhooks/github", content=raw_bytes, headers=headers)
    assert r1.status_code == 202
    assert r1.json()["accepted"] is True
    assert r1.json()["duplicate"] is False

    # Second delivery (duplicate delivery_id)
    r2 = await client.post("/api/v1/webhooks/github", content=raw_bytes, headers=headers)
    assert r2.status_code == 202
    assert r2.json()["accepted"] is True
    assert r2.json()["duplicate"] is True
    assert r2.json()["run_id"] == r1.json()["run_id"]


@pytest.mark.asyncio
async def test_analysis_request_single_dispatch_in_queue_mode(
    client: AsyncClient,
    db: Session,
    audit_project: Project,
    audit_run: Run,
    user_alice: User,
    session_factory,
):
    """Item 2: In queue mode (QUEUE_BACKEND='database'), analysis is dispatched solely

    via the durable queue; background_tasks is NOT called, ensuring exactly one execution.
    """
    from app.models.evidence import EvidenceItem
    from app.models.run import CheckRun

    check = CheckRun(
        run_id=audit_run.id,
        check_key="unit",
        check_name="Unit Tests",
        profile_version=1,
        status="failed",
        exit_code=1,
        duration_ms=100,
        output_truncated=False,
    )
    db.add(check)
    db.flush()
    evidence = EvidenceItem(
        run_id=audit_run.id,
        check_run_id=check.id,
        kind="test_failure",
        source_label="unit:fail",
        content_redacted="AssertionError: expected True got False",
        content_digest="abc123",
        truncated=False,
        byte_length=35,
    )
    db.add(evidence)
    audit_run.lifecycle_status = LifecycleStatus.COMPLETED.value
    audit_run.test_outcome = TestOutcome.FAILED.value
    db.commit()

    run_analysis_mock = MagicMock()
    with patch("app.api.v1.endpoints.analysis.run_analysis", run_analysis_mock):
        resp = await client.post(
            f"/api/v1/runs/{audit_run.id}/analysis/request",
            headers={"x-test-user-id": user_alice.id},
        )
        assert resp.status_code == 202
        # Verify background_tasks was NOT invoked in API process
        assert run_analysis_mock.call_count == 0

    # Verify job is durably queued in database
    db.expire_all()
    queued_job = (
        db.query(Job)
        .filter(Job.run_id == audit_run.id, Job.job_type == JobType.ANALYSIS.value)
        .first()
    )
    assert queued_job is not None
    assert queued_job.status == JobStatus.QUEUED.value

    # Now execute worker and verify run_analysis is called exactly once
    worker = Worker(worker_id="worker-single-dispatch", db_factory=session_factory)
    with patch("app.services.analysis.run_analysis", run_analysis_mock):
        processed = worker.process_one_job()
        assert processed is True
        assert run_analysis_mock.call_count == 1


@pytest.mark.asyncio
async def test_analysis_request_fallback_in_background_mode(
    client: AsyncClient,
    db: Session,
    audit_project: Project,
    audit_run: Run,
    user_alice: User,
):
    """Item 2: In fallback mode (QUEUE_BACKEND='background'), analysis dispatches

    via background_tasks and does NOT enqueue an unhandled queue job.
    """
    from app.models.evidence import EvidenceItem
    from app.models.run import CheckRun

    check = CheckRun(
        run_id=audit_run.id,
        check_key="unit2",
        check_name="Unit Tests",
        profile_version=1,
        status="failed",
        exit_code=1,
        duration_ms=100,
        output_truncated=False,
    )
    db.add(check)
    db.flush()
    evidence = EvidenceItem(
        run_id=audit_run.id,
        check_run_id=check.id,
        kind="test_failure",
        source_label="unit2:fail",
        content_redacted="AssertionError",
        content_digest="abc456",
        truncated=False,
        byte_length=15,
    )
    db.add(evidence)
    audit_run.lifecycle_status = LifecycleStatus.COMPLETED.value
    audit_run.test_outcome = TestOutcome.FAILED.value
    db.commit()

    run_analysis_mock = MagicMock()
    with (
        patch("app.api.v1.endpoints.analysis.settings.QUEUE_BACKEND", "background"),
        patch("app.api.v1.endpoints.analysis.run_analysis", run_analysis_mock),
    ):
        resp = await client.post(
            f"/api/v1/runs/{audit_run.id}/analysis/request",
            headers={"x-test-user-id": user_alice.id},
        )
        assert resp.status_code == 202

    # In fallback mode, no durable queue job was created
    db.expire_all()
    queued_jobs = (
        db.query(Job)
        .filter(Job.run_id == audit_run.id, Job.job_type == JobType.ANALYSIS.value)
        .all()
    )
    assert len(queued_jobs) == 0
