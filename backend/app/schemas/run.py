"""Run and CheckRun schemas matching API_CONTRACTS.md."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CheckRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    check_key: str
    check_name: str
    profile_version: int
    status: str
    exit_code: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    output_truncated: bool
    failure_code: str | None = None
    attempt_number: int


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    delivery_id: str | None = None
    event_kind: str
    pr_number: int | None = None
    base_sha: str | None = None
    head_sha: str
    check_profile_version: int
    lifecycle_status: str
    test_outcome: str
    analysis_outcome: str
    attempt_number: int
    correlation_id: str
    failure_code: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class RunDetail(RunRead):
    checks: list[CheckRunRead] = Field(default_factory=list)
