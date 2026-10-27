"""Project and check profile database models."""

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.run import Run
    from app.models.user import User


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "projects"

    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    github_installation_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    repository_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    repository_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(100), default="main", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "github_installation_id", "repository_id", name="uq_project_installation_repo"
        ),
    )

    # Relationships
    owner: Mapped["User"] = relationship("User", back_populates="projects")
    check_profiles: Mapped[list["ProjectCheckProfile"]] = relationship(
        "ProjectCheckProfile", back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[list["Run"]] = relationship(
        "Run", back_populates="project", cascade="all, delete-orphan"
    )


class ProjectCheckProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "project_check_profiles"

    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), default="default", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    check_definitions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )

    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_project_profile_version"),)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="check_profiles")
