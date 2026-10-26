"""Project endpoints matching API_CONTRACTS.md."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_authorized_project, get_current_user
from app.db.session import get_db
from app.models.project import Project, ProjectCheckProfile
from app.models.user import User
from app.schemas.project import ProjectCheckProfileRead, ProjectCreate, ProjectDetail, ProjectRead

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.get("", response_model=dict[str, list[ProjectRead]])
def list_projects(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Returns only projects the current user is authorized to view."""
    projects = (
        db.query(Project)
        .filter(Project.owner_user_id == current_user.id, Project.active.is_(True))
        .order_by(Project.created_at.desc())
        .all()
    )

    items = [
        ProjectRead(
            id=p.id,
            repository=p.repository_full_name,
            default_branch=p.default_branch,
            created_at=p.created_at,
        )
        for p in projects
    ]
    return {"items": items}


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def create_project(
    data: ProjectCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Register or connect a repository for the authenticated user."""
    # Check if project already exists for this installation/repo
    existing = (
        db.query(Project)
        .filter(
            Project.github_installation_id == data.github_installation_id,
            Project.repository_id == data.repository_id,
        )
        .first()
    )

    if existing:
        return ProjectDetail(
            id=existing.id,
            repository=existing.repository_full_name,
            default_branch=existing.default_branch,
            active=existing.active,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    project = Project(
        owner_user_id=current_user.id,
        github_installation_id=data.github_installation_id,
        repository_id=data.repository_id,
        repository_full_name=data.repository_full_name,
        default_branch=data.default_branch,
        active=True,
    )
    db.add(project)
    db.flush()

    # Create default check profile
    default_profile = ProjectCheckProfile(
        project_id=project.id,
        name="default",
        version=1,
        check_definitions=[
            {
                "key": "unit_tests",
                "name": "Deterministic Unit Tests",
                "command": ["pytest", "tests/unit"],
                "timeout_seconds": 120,
            }
        ],
        created_by_user_id=current_user.id,
    )
    db.add(default_profile)
    db.commit()
    db.refresh(project)

    return ProjectDetail(
        id=project.id,
        repository=project.repository_full_name,
        default_branch=project.default_branch,
        active=project.active,
        created_at=project.created_at,
        updated_at=project.updated_at,
        active_profile=ProjectCheckProfileRead.model_validate(default_profile),
    )


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(
    project: Annotated[Project, Depends(get_authorized_project)],
    db: Annotated[Session, Depends(get_db)],
):
    """Returns repository identity, configured check profiles, and integration health.

    Enforces object-level authorization (returns 403/404 if unauthorized).
    """
    latest_profile = (
        db.query(ProjectCheckProfile)
        .filter(ProjectCheckProfile.project_id == project.id)
        .order_by(ProjectCheckProfile.version.desc())
        .first()
    )

    profile_read = (
        ProjectCheckProfileRead.model_validate(latest_profile) if latest_profile else None
    )

    return ProjectDetail(
        id=project.id,
        repository=project.repository_full_name,
        default_branch=project.default_branch,
        active=project.active,
        created_at=project.created_at,
        updated_at=project.updated_at,
        active_profile=profile_read,
        integration_health="healthy",
    )
