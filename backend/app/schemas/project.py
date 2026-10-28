"""Project schemas matching API_CONTRACTS.md."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ProjectCheckProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: int
    check_definitions: list[dict[str, Any]]
    created_at: datetime


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    repository: str
    default_branch: str
    created_at: datetime


class ProjectDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    repository: str
    default_branch: str
    active: bool
    created_at: datetime
    updated_at: datetime
    active_profile: ProjectCheckProfileRead | None = None
    integration_health: str = "healthy"


class ProjectCreate(BaseModel):
    github_installation_id: int
    repository_id: int
    repository_full_name: str
    default_branch: str = "main"
