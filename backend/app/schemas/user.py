"""User schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    github_user_id: int
    login: str
    display_name: str | None = None
    created_at: datetime


class CurrentUserResponse(BaseModel):
    user: UserRead
    authorized_project_count: int
