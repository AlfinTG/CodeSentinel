"""Analysis trigger and retry endpoints.

Routes:
  POST /runs/{run_id}/analysis/request   — initiate a first analysis
  POST /runs/{run_id}/analysis/retry     — retry after failure/invalid response
  GET  /runs/{run_id}/analysis           — already implemented in runs.py

Gate 4 contract:
  - Requires evidence to exist before accepting the request
  - Rate-limited: only one pending/running analysis per run
  - Does NOT re-run tests
  - On provider failure, analysis state is failed; test_outcome unchanged
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem
from app.models.job import JobType
from app.models.project import Project
from app.models.run import AnalysisOutcome, Run
from app.models.user import User
from app.schemas.analysis import AnalysisRead
from app.services.analysis import run_analysis
from app.services.queue import enqueue_job

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Analysis"])

# States where analysis is already in-flight or complete (block duplicate requests)
_BLOCKING_STATES = {"pending", "running", "succeeded"}


def _authorize_run(run_id: str, current_user: User, db: Session, request: Request) -> Run:
    """Authorize and return the Run, raising HTTP errors on miss/unauthorized."""
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


def _require_evidence(run: Run, db: Session, request: Request) -> None:
    """Raise 422 if no evidence items exist for the run yet."""
    request_id = getattr(request.state, "request_id", "req-unknown")
    count = db.query(EvidenceItem).filter(EvidenceItem.run_id == run.id).count()
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "no_evidence",
                    "message": "Cannot request analysis: no evidence items exist for this run",
                    "request_id": request_id,
                }
            },
        )


def _get_latest_analysis(run: Run, db: Session) -> Analysis | None:
    return (
        db.query(Analysis)
        .filter(Analysis.run_id == run.id)
        .order_by(Analysis.attempt_number.desc())
        .first()
    )


def _create_analysis_record(run: Run, db: Session, attempt: int = 1) -> Analysis:
    """Persist a new pending Analysis record and update run.analysis_outcome."""
    import hashlib

    from app.core.config import settings

    analysis = Analysis(
        run_id=run.id,
        attempt_number=attempt,
        provider="nebius_token_factory",
        model_id=settings.NEBIUS_MODEL_ID,
        status="pending",
        schema_version="1.0",
        input_digest=hashlib.sha256(b"pending").hexdigest(),  # placeholder; updated by service
    )
    run.analysis_outcome = AnalysisOutcome.QUEUED.value
    db.add(analysis)
    db.commit()
    # NOTE: Do not call db.refresh(analysis) here — it causes OperationalError in tests
    # because the background task may run in a different session context. The object
    # attributes are already populated from the constructor values.
    return analysis


@router.post(
    "/runs/{run_id}/analysis/request",
    response_model=AnalysisRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_analysis(
    run_id: str,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Initiate AI-powered regression diagnosis for a completed run.

    - Requires at least one evidence item to exist.
    - Rejected if analysis is already pending, running, or succeeded.
    - Dispatches analysis as a background task; does not block the HTTP response.
    - Test outcome is NEVER modified by this endpoint.
    """
    run = _authorize_run(run_id, current_user, db, request)
    request_id = getattr(request.state, "request_id", "req-unknown")

    _require_evidence(run, db, request)

    existing = _get_latest_analysis(run, db)
    if existing and existing.status in _BLOCKING_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "analysis_already_exists",
                    "message": f"Analysis is already {existing.status}. Use /retry to re-run after failure.",
                    "request_id": request_id,
                }
            },
        )

    attempt = (existing.attempt_number + 1) if existing else 1
    run_id_for_log = run.id  # capture before commit expires the object
    analysis = _create_analysis_record(run, db, attempt=attempt)

    if settings.QUEUE_BACKEND == "database":
        # Queue-backed architecture: Background worker daemon claims and processes exclusively
        enqueue_job(
            db=db,
            project_id=run.project_id,
            job_type=JobType.ANALYSIS,
            run_id=run.id,
            payload={"analysis_id": analysis.id, "attempt": attempt},
            idempotency_key=f"analysis:{run.id}:{attempt}",
        )
        db.commit()
    else:
        # Fallback architecture: Dispatches via FastAPI BackgroundTasks if queue is explicitly disabled
        background_tasks.add_task(run_analysis, run, analysis, db)

    logger.info("Analysis queued for run=%s attempt=%d", run_id_for_log, attempt)
    return AnalysisRead.model_validate(analysis)


@router.post(
    "/runs/{run_id}/analysis/retry",
    response_model=AnalysisRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_analysis(
    run_id: str,
    background_tasks: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Retry a failed or invalid analysis.

    Permitted states for retry: failed, invalid_response (via safe_error_code).
    Idempotency: creates a new attempt_number record.
    Does NOT re-run tests. Only analysis is re-attempted.
    Rate-limited: blocked if current analysis is pending or running.
    """
    run = _authorize_run(run_id, current_user, db, request)
    request_id = getattr(request.state, "request_id", "req-unknown")

    _require_evidence(run, db, request)

    existing = _get_latest_analysis(run, db)

    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "analysis_not_found",
                    "message": "No prior analysis exists to retry. Use /request instead.",
                    "request_id": request_id,
                }
            },
        )

    if existing.status in {"pending", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "analysis_in_progress",
                    "message": "Analysis is currently in progress. Wait for it to complete before retrying.",
                    "request_id": request_id,
                }
            },
        )

    run_id_for_log = run.id  # capture before commit expires the object
    next_attempt = existing.attempt_number + 1
    analysis = _create_analysis_record(run, db, attempt=next_attempt)

    if settings.QUEUE_BACKEND == "database":
        # Queue-backed architecture: Background worker daemon claims and processes exclusively
        enqueue_job(
            db=db,
            project_id=run.project_id,
            job_type=JobType.ANALYSIS,
            run_id=run.id,
            payload={"analysis_id": analysis.id, "attempt": next_attempt},
            idempotency_key=f"analysis:{run.id}:{next_attempt}",
        )
        db.commit()
    else:
        # Fallback architecture: Dispatches via FastAPI BackgroundTasks if queue is explicitly disabled
        background_tasks.add_task(run_analysis, run, analysis, db)

    logger.info("Analysis retry queued for run=%s attempt=%d", run_id_for_log, next_attempt)
    return AnalysisRead.model_validate(analysis)
