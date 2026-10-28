"""Evidence item schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EvidenceItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    check_run_id: str | None = None
    kind: str
    source_label: str
    content_redacted: str
    content_digest: str
    truncated: bool
    byte_length: int = Field(..., le=262144, description="Enforce maximum 256 KiB cap")
    created_at: datetime


class EvidenceItemCreate(BaseModel):
    kind: str
    source_label: str
    content_redacted: str
    content_digest: str
    truncated: bool = False
    byte_length: int
    check_run_id: str | None = None
