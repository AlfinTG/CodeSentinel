"""Evidence item database model."""

import enum
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.run import CheckRun, Run


class EvidenceKind(str, enum.Enum):
    FAILED_ASSERTION = "failed_assertion"
    STACK_TRACE = "stack_trace"
    TEST_REPORT = "test_report"
    LOG_EXCERPT = "log_excerpt"
    ROUTE_RESULT = "route_result"
    DIFF_EXCERPT = "diff_excerpt"


class EvidenceItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "evidence_items"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id"), nullable=False, index=True
    )
    check_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("check_runs.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    content_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    run: Mapped["Run"] = relationship("Run", back_populates="evidence")
    check_run: Mapped[Optional["CheckRun"]] = relationship("CheckRun", back_populates="evidence")
