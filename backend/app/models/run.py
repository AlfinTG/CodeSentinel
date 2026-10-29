"""Run and check run database models."""

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.analysis import Analysis
    from app.models.evidence import EvidenceItem
    from app.models.job import Job
    from app.models.project import Project


class LifecycleStatus(str, enum.Enum):
    RECEIVED = "received"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INCONCLUSIVE = "inconclusive"


class TestOutcome(str, enum.Enum):
    __test__ = False

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class AnalysisOutcome(str, enum.Enum):
    NOT_REQUESTED = "not_requested"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"


class Run(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "runs"

    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    delivery_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    event_kind: Mapped[str] = mapped_column(String(50), default="push", nullable=False)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    check_profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    lifecycle_status: Mapped[str] = mapped_column(
        String(30), default=LifecycleStatus.RECEIVED.value, nullable=False
    )
    test_outcome: Mapped[str] = mapped_column(
        String(30), default=TestOutcome.PENDING.value, nullable=False
    )
    analysis_outcome: Mapped[str] = mapped_column(
        String(30), default=AnalysisOutcome.NOT_REQUESTED.value, nullable=False
    )

    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        Index("ix_runs_project_created", "project_id", "created_at"),
        Index("ix_runs_lifecycle_queued", "lifecycle_status", "queued_at"),
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="runs")
    checks: Mapped[list["CheckRun"]] = relationship(
        "CheckRun", back_populates="run", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["EvidenceItem"]] = relationship(
        "EvidenceItem", back_populates="run", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["Analysis"]] = relationship(
        "Analysis", back_populates="run", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="run", cascade="all, delete-orphan"
    )


class CheckRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "check_runs"

    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False)
    check_key: Mapped[str] = mapped_column(String(100), nullable=False)
    check_name: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "check_key", "attempt_number", name="uq_check_run_attempt"),
    )

    # Relationships
    run: Mapped["Run"] = relationship("Run", back_populates="checks")
    evidence: Mapped[list["EvidenceItem"]] = relationship(
        "EvidenceItem", back_populates="check_run"
    )
