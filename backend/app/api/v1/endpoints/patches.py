"""Candidate Patch Attempt API endpoints (Phase 6 / P1 Scope)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.patch import PatchAttempt
from app.models.project import Project
from app.models.run import Run
from app.models.user import User
from app.schemas.patch import PatchAttemptCreate, PatchAttemptRead
from app.services.patch import execute_patch_verification, generate_candidate_patch

router = APIRouter(tags=["Candidate Patches (P1)"])


def _authorize_run_access(run_id: str, current_user: User, db: Session, request: Request) -> tuple[Run, Project]:
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
    return run, project


@router.post(
    "/runs/{run_id}/patch-attempts",
    response_model=PatchAttemptRead,
    status_code=status.HTTP_201_CREATED,
)
def create_patch_attempt(
    run_id: str,
    data: PatchAttemptCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Propose candidate patch and run isolated verification.

    - Authorizes project ownership
    - Generates candidate unified diff
    - Applies diff in isolated ephemeral workspace
    - Executes required check profiles
    - Returns verified status ONLY if all checks exit 0.
    """
    run, project = _authorize_run_access(run_id, current_user, db, request)

    # Fetch latest diagnosis if available
    latest_analysis = (
        db.query(Analysis)
        .filter(Analysis.run_id == run.id)
        .order_by(Analysis.attempt_number.desc())
        .first()
    )

    diff_text, summary = generate_candidate_patch(
        run, latest_analysis, data.custom_instructions
    )

    import hashlib
    candidate_digest = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()

    attempt = PatchAttempt(
        run_id=run.id,
        requested_by_user_id=current_user.id,
        status="proposed",
        base_sha=run.head_sha,
        candidate_digest=candidate_digest,
        workspace_reference="ephemeral:pending",
        summary=summary,
        diff_content=diff_text,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    # Run isolated verification
    verified_attempt = execute_patch_verification(attempt, run, project, db)
    return PatchAttemptRead.model_validate(verified_attempt)


@router.get(
    "/runs/{run_id}/patch-attempts",
    response_model=list[PatchAttemptRead],
)
def list_run_patch_attempts(
    run_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """List all candidate patch attempts for a run."""
    run, _ = _authorize_run_access(run_id, current_user, db, request)
    attempts = (
        db.query(PatchAttempt)
        .filter(PatchAttempt.run_id == run.id)
        .order_by(PatchAttempt.created_at.desc())
        .all()
    )
    return [PatchAttemptRead.model_validate(a) for a in attempts]


@router.get(
    "/patch-attempts/{attempt_id}",
    response_model=PatchAttemptRead,
)
def get_patch_attempt(
    attempt_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request: Request,
):
    """Retrieve detailed candidate patch attempt metadata, diff, and check outcomes."""
    request_id = getattr(request.state, "request_id", "req-unknown")
    attempt = db.query(PatchAttempt).filter(PatchAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "patch_attempt_not_found",
                    "message": "Patch attempt not found",
                    "request_id": request_id,
                }
            },
        )

    # Authorize through parent run & project
    _authorize_run_access(attempt.run_id, current_user, db, request)
    return PatchAttemptRead.model_validate(attempt)

