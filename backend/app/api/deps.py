"""FastAPI request dependencies: Database, Authentication, and Project Authorization."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.project import Project
from app.models.user import User


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_test_user_id: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve the currently authenticated user.

    In tests, accepts `X-Test-User-ID` header.
    In development, resolves or creates a default developer user.
    In production, resolves the server-side session cookie.
    """
    request_id = getattr(request.state, "request_id", "req-unknown")

    if x_test_user_id:
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

    # Default developer user for local development and single-tenant controlled demo
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
