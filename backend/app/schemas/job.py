"""Pydantic schemas for durable queue jobs."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobBase(BaseModel):
    job_type: str = Field("scan", description="Type of job: scan or analysis")
    priority: int = Field(0, description="Priority where higher values are claimed first")


class JobCreateRequest(JobBase):
    run_id: str | None = Field(None, description="Linked run ID if applicable")
    payload: dict[str, Any] | None = Field(None, description="Optional parameters for job execution")
    idempotency_key: str | None = Field(
        None, max_length=100, description="Unique client or event delivery key"
    )
    max_attempts: int = Field(3, ge=1, le=10, description="Maximum execution attempts")
    retry_delay_seconds: int = Field(
        5, ge=1, le=300, description="Base backoff delay between retry attempts"
    )
    timeout_seconds: int = Field(
        300, ge=10, le=1800, description="Maximum execution time before timeout"
    )


class JobRead(JobBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    run_id: str | None = None
    status: str
    attempt_number: int
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class JobDetail(JobRead):
    payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    timeout_seconds: int
    retry_delay_seconds: int
    next_attempt_at: datetime | None = None
    locked_by: str | None = None
    locked_at: datetime | None = None


class JobCancelResponse(BaseModel):
    job_id: str
    status: str
    message: str
