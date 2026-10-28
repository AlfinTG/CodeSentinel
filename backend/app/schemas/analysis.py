"""Canonical Model Diagnosis schemas matching Section 3 of API_CONTRACTS.md."""

import enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HypothesisConfidence(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RootCauseHypothesis(BaseModel):
    statement: str = Field(
        ..., max_length=1000, description="Plausible cause, clearly marked as hypothesis"
    )
    evidence_ids: list[str] = Field(
        ..., min_length=1, max_length=20, description="Referenced evidence UUIDs from this run"
    )
    confidence: HypothesisConfidence = Field(
        ..., description="Qualitative confidence, not a probability"
    )
    reasoning: str = Field(..., max_length=2000, description="Brief evidence-linked rationale")


class ModelDiagnosisSchema(BaseModel):
    """Strict canonical diagnosis structure produced by Nebius Token Factory model."""

    summary: str = Field(
        ..., max_length=500, description="One or two sentence summary grounded in evidence"
    )
    root_cause_hypotheses: list[RootCauseHypothesis] = Field(..., max_length=5)
    affected_behaviors: list[str] = Field(default_factory=list, max_length=10)
    recommended_next_steps: list[str] = Field(default_factory=list, max_length=10)
    missing_evidence: list[str] = Field(default_factory=list, max_length=10)


class AnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    run_id: str
    attempt_number: int
    provider: str
    model_id: str
    status: str
    schema_version: str
    result_json: ModelDiagnosisSchema | None = None
    input_digest: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    latency_ms: int | None = None
    safe_error_code: str | None = None
