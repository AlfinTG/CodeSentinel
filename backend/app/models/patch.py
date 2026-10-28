"""Candidate patch attempt database model (P1 scope)."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PatchAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "patch_attempts"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), default="proposed", nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    diff_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_results: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
