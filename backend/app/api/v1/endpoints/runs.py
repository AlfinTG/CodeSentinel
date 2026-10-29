"""Run, check, evidence, and analysis endpoints matching API_CONTRACTS.md."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_authorized_project, get_current_user
from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem
from app.models.job import Job, JobStatus, JobType
from app.models.project import Project
from app.models.run import CheckRun, LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.schemas.analysis import AnalysisRead
from app.schemas.common import PaginatedResponse
from app.schemas.evidence import EvidenceItemRead
from app.schemas.job import JobRead
from app.schemas.run import CheckRunRead, RunDetail, RunRead
from app.services.queue import cancel_job, enqueue_job

router = APIRouter(tags=["Runs & Evidence"])


def _authorize_run_access(run_id: str, current_user: User, db: Session, request: Request) -> Run:
    """Helper to enforce project-level authorization on a run."""
    request_id = getattr(request.state, "request_id", "req-unknown")
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "run_not_found",
                    "message": "Run not found",
                    "request_id": request_id,
                }
            },
        )

    project = db.query(Project).filter(Project.id == run.project_id).first()
    if not project or project.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "forbidden",
                    "message": "Access denied to run",
                    "request_id": request_id,
                }
            },
        )
    return run


@router.get("/projects/{project_id}/runs", response_model=PaginatedResponse[RunRead])
def list_project_runs(
    project: Annotated[Project, Depends(get_authorized_project)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
):
    """Returns runs for the authorized project with cursor pagination.

    Stable ordering by creation time + ID.
    """
    query = db.query(Run).filter(Run.project_id == project.id)

    if cursor:
        query = query.filter(Run.created_at < cursor)

    runs = query.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit + 1).all()

    has_more = len(runs) > limit
    items = runs[:limit]
    next_cursor = items[-1].created_at.isoformat() if has_more and items else None

    return PaginatedResponse[RunRead](
        items=[RunRead.model_validate(r) for r in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Returns immutable source identifiers, lifecycle, test/analysis outcomes, and attempt summaries.

    Does not inline unbounded logs.
    """
    run = _authorize_run_access(run_id, current_user, db, request)
    return RunDetail.model_validate(run)


@router.get("/runs/{run_id}/checks", response_model=list[CheckRunRead])
def get_run_checks(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Returns configured check results, exit code, duration, and truncation flag."""
    run = _authorize_run_access(run_id, current_user, db, request)
    checks = db.query(CheckRun).filter(CheckRun.run_id == run.id).all()
    return [CheckRunRead.model_validate(c) for c in checks]


@router.get("/checks/{check_id}/evidence", response_model=PaginatedResponse[EvidenceItemRead])
def get_check_evidence(
    check_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
    limit: int = Query(20, ge=1, le=50),
):
    """Returns redacted evidence metadata and bounded content for a check.

    Enforces parent run and project authorization.
    """
    request_id = getattr(request.state, "request_id", "req-unknown")
    check = db.query(CheckRun).filter(CheckRun.id == check_id).first()
    if not check:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "check_not_found",
                    "message": "Check not found",
                    "request_id": request_id,
                }
            },
        )

    # Authorize parent run
    _authorize_run_access(check.run_id, current_user, db, request)

    evidence_items = (
        db.query(EvidenceItem)
        .filter(EvidenceItem.check_run_id == check.id)
        .order_by(EvidenceItem.created_at.asc())
        .limit(limit)
        .all()
    )

    return PaginatedResponse[EvidenceItemRead](
        items=[EvidenceItemRead.model_validate(e) for e in evidence_items],
        has_more=False,
    )


@router.get("/runs/{run_id}/analysis", response_model=AnalysisRead)
def get_run_analysis(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Returns analysis status and validated structured diagnosis when present.

    Model hypotheses remain labeled as hypotheses; never confused with deterministic test results.
    """
    request_id = getattr(request.state, "request_id", "req-unknown")
    run = _authorize_run_access(run_id, current_user, db, request)

    analysis = (
        db.query(Analysis)
        .filter(Analysis.run_id == run.id)
        .order_by(Analysis.attempt_number.desc())
        .first()
    )

    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "analysis_not_found",
                    "message": "No analysis requested or available for this run",
                    "request_id": request_id,
                }
            },
        )

    return AnalysisRead.model_validate(analysis)


@router.get("/runs/{run_id}/evidence", response_model=PaginatedResponse[EvidenceItemRead])
def get_run_all_evidence(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
    limit: int = Query(50, ge=1, le=100),
):
    """Returns all evidence items for a run, ordered by creation time.

    Supports cross-referencing model hypothesis evidence_ids.
    """
    run = _authorize_run_access(run_id, current_user, db, request)
    evidence_items = (
        db.query(EvidenceItem)
        .filter(EvidenceItem.run_id == run.id)
        .order_by(EvidenceItem.created_at.asc())
        .limit(limit)
        .all()
    )
    return PaginatedResponse[EvidenceItemRead](
        items=[EvidenceItemRead.model_validate(e) for e in evidence_items],
        has_more=False,
    )


@router.get("/runs/{run_id}/job", response_model=JobRead)
def get_run_job(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Returns the latest queue job associated with this run."""
    request_id = getattr(request.state, "request_id", "req-unknown")
    run = _authorize_run_access(run_id, current_user, db, request)

    job = (
        db.query(Job)
        .filter(Job.run_id == run.id)
        .order_by(Job.created_at.desc())
        .first()
    )

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "job_not_found",
                    "message": "No queue job found for this run",
                    "request_id": request_id,
                }
            },
        )

    return JobRead.model_validate(job)


@router.post("/runs/{run_id}/enqueue", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def enqueue_run_job(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Enqueues or re-enqueues an asynchronous scan job for this run."""
    run = _authorize_run_access(run_id, current_user, db, request)
    job = enqueue_job(
        db=db,
        project_id=run.project_id,
        job_type=JobType.SCAN,
        run_id=run.id,
    )
    db.commit()
    db.refresh(job)
    return JobRead.model_validate(job)


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Cancel a queued or running run and its associated queue job per API_CONTRACTS.md."""
    request_id = getattr(request.state, "request_id", "req-unknown")
    run = _authorize_run_access(run_id, current_user, db, request)

    if run.lifecycle_status not in (
        LifecycleStatus.RECEIVED.value,
        LifecycleStatus.QUEUED.value,
        LifecycleStatus.RUNNING.value,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "invalid_state",
                    "message": f"Run cannot be cancelled in state '{run.lifecycle_status}'",
                    "request_id": request_id,
                }
            },
        )

    # Cancel any active or queued jobs associated with this run
    active_jobs = (
        db.query(Job)
        .filter(
            Job.run_id == run.id,
            Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        )
        .all()
    )
    for j in active_jobs:
        cancel_job(db, j.id)

    run.lifecycle_status = LifecycleStatus.CANCELLED.value
    run.completed_at = datetime.now(UTC)

    if run.test_outcome == TestOutcome.PENDING.value:
        run.test_outcome = TestOutcome.CANCELLED.value

    db.commit()
    db.refresh(run)

    return {
        "id": run.id,
        "run_id": run.id,
        "lifecycle_status": run.lifecycle_status,
        "test_outcome": run.test_outcome,
        "message": "Run cancelled successfully",
    }
