"""Endpoints for managing and querying asynchronous queue jobs."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_authorized_project, get_current_user
from app.db.session import get_db
from app.models.job import Job
from app.models.project import Project
from app.models.user import User
from app.schemas.job import JobCancelResponse, JobCreateRequest, JobDetail, JobRead
from app.services.queue import cancel_job, enqueue_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["Distributed Queue & Jobs"])


def _authorize_job(job_id: str, current_user: User, db: Session, request: Request) -> Job:
    """Authorize access to a job via its project ownership."""
    request_id = getattr(request.state, "request_id", "req-unknown")
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "job_not_found",
                    "message": "Job not found",
                    "request_id": request_id,
                }
            },
        )

    project = db.query(Project).filter(Project.id == job.project_id).first()
    if not project or project.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "forbidden",
                    "message": "Access denied to job",
                    "request_id": request_id,
                }
            },
        )
    return job


@router.get("/{job_id}", response_model=JobDetail)
def get_job_detail(
    job_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Retrieve full lifecycle status, attempts, error info, and execution details for a job."""
    job = _authorize_job(job_id, current_user, db, request)
    return JobDetail.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobCancelResponse)
def cancel_job_endpoint(
    job_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Cancel a queued or running job."""
    request_id = getattr(request.state, "request_id", "req-unknown")
    job = _authorize_job(job_id, current_user, db, request)

    try:
        cancelled = cancel_job(db, job.id)
        return JobCancelResponse(
            job_id=cancelled.id,
            status=cancelled.status,
            message="Job cancelled successfully",
        )
    except Exception as exc:
        logger.warning("Failed to cancel job %s: %s", job.id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "invalid_transition",
                    "message": "Job cannot be cancelled in its current state",
                    "request_id": request_id,
                }
            },
        ) from exc


@router.post(
    "/projects/{project_id}/enqueue",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_project_job(
    project: Annotated[Project, Depends(get_authorized_project)],
    payload: JobCreateRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Create a new durable queue job for an authorized project."""
    job = enqueue_job(
        db=db,
        project_id=project.id,
        job_type=payload.job_type,
        run_id=payload.run_id,
        payload=payload.payload,
        idempotency_key=payload.idempotency_key,
        max_attempts=payload.max_attempts,
        retry_delay_seconds=payload.retry_delay_seconds,
        priority=payload.priority,
        timeout_seconds=payload.timeout_seconds,
    )
    db.commit()
    db.refresh(job)
    return JobRead.model_validate(job)
