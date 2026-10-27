"""GitHub webhook delivery database model."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class GitHubDelivery(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "github_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(50), nullable=False)
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    repository_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    payload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    processing_status: Mapped[str] = mapped_column(String(30), default="received", nullable=False)
    linked_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("runs.id"), nullable=True
    )
