"""Candidate patch attempt database model (P1 scope)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
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
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
