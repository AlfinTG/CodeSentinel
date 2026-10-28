"""Schemas for Phase 6 Candidate Patch Verification."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PatchValidationCheck(BaseModel):
    check_name: str
    status: str
    exit_code: int | None
    duration_ms: int
    output_truncated: bool = False


class PatchValidationSummary(BaseModel):
    all_passed: bool
    checks: list[PatchValidationCheck] = Field(default_factory=list)
    failure_reason: str | None = None


class PatchAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    requested_by_user_id: str
    status: str  # proposed, validating, verified, failed, timed_out, unverified
    base_sha: str
    candidate_digest: str
    workspace_reference: str
    summary: str | None = None
    diff_content: str | None = None
    validation_results: dict[str, Any] | None = None
    validated_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime


class PatchAttemptCreate(BaseModel):
    custom_instructions: str | None = Field(
        default=None,
        max_length=500,
        description="Optional maintainer instructions for candidate repair",
    )

