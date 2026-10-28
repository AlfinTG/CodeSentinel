"""Common schemas, error models, and pagination helpers."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str = Field(..., description="Stable snake_case machine error code")
    message: str = Field(..., description="Safe human-readable error description")
    request_id: str = Field(..., description="Unique correlation request identifier")


class ErrorResponse(BaseModel):
    error: ErrorDetail


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False
    total_count: int | None = None
