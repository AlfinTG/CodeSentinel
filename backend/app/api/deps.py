"""FastAPI request dependencies: Database, Authentication, and Project Authorization."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.services.session import verify_session_token


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_test_user_id: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the currently authenticated user.

    In non-production tests, accepts `X-Test-User-ID` header.
    In all environments, resolves and validates the encrypted session cookie.
    In production, strictly rejects unauthenticated requests with 401 Unauthorized.
    In development, falls back to the synthetic demo-dev user only if enabled.
    """
    request_id = getattr(request.state, "request_id", "req-unknown")
    is_production = settings.CODESENTINEL_ENV == "production"

    # Test suite bypass: allow X-Test-User-ID only in non-production environments
    if not is_production and x_test_user_id:
        user = db.query(User).filter(User.id == x_test_user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": {
                        "code": "unauthorized",
                        "message": "User not found",
                        "request_id": request_id,
                    }
                },
            )
        return user

    # Resolve encrypted session cookie
    session_cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_cookie:
        session_data = verify_session_token(
            token=session_cookie,
            max_age=settings.SESSION_MAX_AGE_SECONDS,
        )
        if session_data and "user_id" in session_data:
            user = db.query(User).filter(User.id == session_data["user_id"]).first()
            if user:
                return user

    # In production, unauthenticated requests are strictly rejected
    if is_production:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "Authentication required. Please sign in with GitHub.",
                    "request_id": request_id,
                }
            },
        )

    # In development/test, fallback to demo-dev if explicitly allowed
    if settings.ALLOW_DEMO_DEV_FALLBACK:
        user = db.query(User).filter(User.login == "demo-dev").first()
        if not user:
            user = User(
                github_user_id=1001,
                login="demo-dev",
                display_name="Demo Developer",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "unauthorized",
                "message": "Authentication required",
                "request_id": request_id,
            }
        },
    )


def get_authorized_project(
    project_id: str,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
) -> Project:
    """Enforce strict object-level authorization for project access.

    Raises 404 (or 403) if the project does not exist or does not belong to the user.
    """
    request_id = getattr(request.state, "request_id", "req-unknown")
    project = db.query(Project).filter(Project.id == project_id).first()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "project_not_found",
                    "message": "Project not found",
                    "request_id": request_id,
                }
            },
        )

    # Cross-project authorization check
    if project.owner_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "forbidden",
                    "message": "You are not authorized to access this project",
                    "request_id": request_id,
                }
            },
        )

    return project
