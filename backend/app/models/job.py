"""Durable queue job database model for Phase 2 distributed worker architecture."""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.run import Run


class JobStatus(str, enum.Enum):
    """Lifecycle states for asynchronous queue jobs."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class JobType(str, enum.Enum):
    """Categorization of background workloads."""

    SCAN = "scan"
    ANALYSIS = "analysis"


class Job(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Persistent, durable record of an asynchronous task."""

    __tablename__ = "jobs"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("runs.id"), nullable=True, index=True
    )

    job_type: Mapped[str] = mapped_column(String(50), default=JobType.SCAN.value, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=JobStatus.QUEUED.value, nullable=False, index=True
    )

    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    attempt_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)

    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True, unique=True, index=True
    )

    __table_args__ = (
        Index("ix_jobs_claim_lookup", "status", "next_attempt_at", "priority"),
        Index("ix_jobs_run_type", "run_id", "job_type"),
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project")
    run: Mapped["Run | None"] = relationship("Run", back_populates="jobs")
