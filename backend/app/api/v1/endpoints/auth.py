"""Authentication and identity endpoints per API_CONTRACTS.md."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.project import Project
from app.models.user import User
from app.schemas.user import CurrentUserResponse, UserRead

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.get("/me", response_model=CurrentUserResponse)
def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Returns current user identity and authorized project count.

    Never exposes tokens, private keys, or installation secrets.
    """
    authorized_count = (
        db.query(Project)
        .filter(Project.owner_user_id == current_user.id, Project.active.is_(True))
        .count()
    )

    return CurrentUserResponse(
        user=UserRead.model_validate(current_user),
        authorized_project_count=authorized_count,
    )


@router.post("/logout")
def logout(response: Response):
    """Clears authentication session cookies."""
    response.delete_cookie("codesentinel_session")
    return {"status": "logged_out"}
