"""Integration and unit tests for NVIDIA NIM API provider.

Tests:
- Missing credentials handling
- Authentication errors (401/403) and permanent failure handling
- Timeouts, rate limits (429), and transient retry backoff
- Malformed model responses and strict schema validation
- Secret redaction and credential safety
- Opt-in live inference test against https://integrate.api.nvidia.com/v1
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.secrets import is_sensitive_key, redact_sensitive_dict
from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem, EvidenceKind
from app.models.project import Project
from app.models.run import AnalysisOutcome, LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.schemas.analysis import ModelDiagnosisSchema
from app.services.analysis import (
    _call_nvidia,
    nvidia_circuit_breaker,
    run_analysis,
)
from app.services.circuit_breaker import CircuitState

logger = logging.getLogger(__name__)

# Condition for executing live provider test
LIVE_NVIDIA_ENABLED = bool(
    settings.NVIDIA_API_KEY
    and "api.nvidia.com" in settings.NVIDIA_BASE_URL
    and os.environ.get("RUN_LIVE_NVIDIA_TESTS", "").lower() in ("1", "true", "yes")
)


@pytest.fixture
def run_with_evidence(db: Session) -> tuple[Run, list[EvidenceItem]]:
    """Create a test run with bounded failure evidence."""
    uid = uuid.uuid4().int % 10000000
    user = User(github_user_id=8800 + uid, login=f"nvidia-test-user-{uid}", display_name="NVIDIA Test")
    db.add(user)
    db.commit()
    db.refresh(user)

    project = Project(
        owner_user_id=user.id,
        github_installation_id=98800 + uid,
        repository_id=98800 + uid,
        repository_full_name=f"{user.login}/nvidia-test-repo",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    run = Run(
        project_id=project.id,
        head_sha="a1b2c3d4e5f60000111122223333444455556666",
        correlation_id=f"corr-{uid}",
        event_kind="push",
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.FAILED.value,
        analysis_outcome=AnalysisOutcome.QUEUED.value,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    items = []
    for i in range(2):
        content = f"AssertionError in test_case_{i}: expected True got False"
        item = EvidenceItem(
            run_id=run.id,
            kind=EvidenceKind.FAILED_ASSERTION.value,
            source_label=f"test_file_{i}.py",
            content_redacted=content,
            content_digest=hashlib.sha256(content.encode()).hexdigest(),
            truncated=False,
            byte_length=len(content.encode()),
        )
        db.add(item)
        items.append(item)
    db.commit()
    for item in items:
        db.refresh(item)

    return run, items


# ──────────────────────────────────────────────────────────────────────────────
# 1. Secret Governance & Redaction Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_nvidia_api_key_is_sensitive():
    """Verify that NVIDIA_API_KEY is recognized by secret governance patterns."""
    assert is_sensitive_key("NVIDIA_API_KEY") is True
    assert is_sensitive_key("nvidia_api_key") is True

    data = {
        "provider": "nvidia",
        "NVIDIA_API_KEY": "nvapi-secret-key-12345",
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
    }
    redacted = redact_sensitive_dict(data)
    assert redacted["NVIDIA_API_KEY"] == "***REDACTED***"
    assert redacted["provider"] == "nvidia"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Missing Credentials Handling
# ──────────────────────────────────────────────────────────────────────────────

def test_missing_nvidia_api_key_returns_provider_unavailable(monkeypatch):
    """When NVIDIA_API_KEY is not configured, _call_nvidia fast-fails safely."""
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")

    result = _call_nvidia("Test user prompt")
    raw, latency, tokens = result

    assert raw is None
    assert latency == 0
    assert result.error_code == "provider_unavailable"
    assert result.is_transient is False


def test_missing_nvidia_api_key_in_pipeline(db: Session, run_with_evidence: tuple[Run, list[EvidenceItem]], monkeypatch):
    """run_analysis marks analysis as failed with provider_unavailable without crashing."""
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(settings, "AI_PROVIDER", "nvidia")
    run, evidence = run_with_evidence

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nvidia",
        model_id=settings.NVIDIA_MODEL_ID,
        input_digest="missing-key-digest",
        status="pending",
    )
    db.add(analysis)
    db.commit()

    run_analysis(run, analysis, db)

    db.refresh(analysis)
    db.refresh(run)

    assert analysis.status == "failed"
    assert analysis.safe_error_code == "provider_unavailable"
    assert run.test_outcome == TestOutcome.FAILED.value  # Gate 4: test outcome unchanged


# ──────────────────────────────────────────────────────────────────────────────
# 3. Permanent Authentication Errors (401 / 403)
# ──────────────────────────────────────────────────────────────────────────────

def test_nvidia_permanent_401_auth_error_not_retried(monkeypatch):
    """401 responses are classified as permanent provider_auth_error and not retried."""
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "invalid-key")
    call_count = 0

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=401,
            json={"error": "Unauthorized"},
            request=httpx.Request("POST", "http://test"),
        )

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nvidia("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 1  # Not retried
    assert result.error_code == "provider_auth_error"
    assert result.is_transient is False


def test_nvidia_auth_error_does_not_trip_circuit_breaker(db: Session, run_with_evidence: tuple[Run, list[EvidenceItem]]):
    """Permanent authentication errors release the probe slot without tripping circuit breaker."""
    run, evidence = run_with_evidence
    nvidia_circuit_breaker.reset()
    nvidia_circuit_breaker.failure_threshold = 2

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nvidia",
        model_id=settings.NVIDIA_MODEL_ID,
        input_digest="auth-error-digest",
        status="pending",
    )
    db.add(analysis)
    db.commit()

    from app.services.analysis import NebiusCallResult
    auth_err_result = NebiusCallResult(
        None, 50, {}, error_code="provider_auth_error", is_transient=False
    )

    from app.services import analysis as analysis_svc
    with patch.object(analysis_svc, "_call_nvidia", return_value=auth_err_result):
        analysis_svc.run_analysis(run, analysis, db)

    db.refresh(analysis)
    assert analysis.status == "failed"
    assert analysis.safe_error_code == "provider_auth_error"
    assert nvidia_circuit_breaker.state == CircuitState.CLOSED
    assert nvidia_circuit_breaker.consecutive_failures == 0


# ──────────────────────────────────────────────────────────────────────────────
# 4. Transient Retries & Timeouts
# ──────────────────────────────────────────────────────────────────────────────

def test_nvidia_transient_500_retries_with_backoff(monkeypatch):
    """500 server errors are retried up to NVIDIA_MAX_RETRIES."""
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "valid-looking-key")
    monkeypatch.setattr(settings, "NVIDIA_MAX_RETRIES", 2)

    call_count = 0
    slept_delays = []

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=500, request=httpx.Request("POST", "http://test"))

    from app.services import analysis as analysis_svc
    monkeypatch.setattr(analysis_svc, "_backoff_sleep", lambda s: slept_delays.append(s))

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nvidia("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 3  # 1 initial + 2 retries
    assert len(slept_delays) == 2
    assert result.is_transient is True
    assert result.error_code == "provider_unavailable"


def test_nvidia_timeout_retries_and_marks_transient(monkeypatch):
    """Timeouts are retried and marked as transient."""
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "dummy-key")
    monkeypatch.setattr(settings, "NVIDIA_MAX_RETRIES", 1)

    call_count = 0
    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise httpx.TimeoutException("Read timed out")

    from app.services import analysis as analysis_svc
    sleep_mock = MagicMock()
    monkeypatch.setattr(analysis_svc, "_backoff_sleep", sleep_mock)

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nvidia("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 2
    assert sleep_mock.call_count == 1
    assert result.is_transient is True
    assert result.error_code == "provider_timeout"


# ──────────────────────────────────────────────────────────────────────────────
# 5. Malformed Model Response & Schema Validation
# ──────────────────────────────────────────────────────────────────────────────

def test_nvidia_malformed_model_response_handling(db: Session, run_with_evidence: tuple[Run, list[EvidenceItem]]):
    """Invalid JSON schema from NVIDIA model produces invalid_model_response safe error."""
    run, evidence = run_with_evidence

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nvidia",
        model_id=settings.NVIDIA_MODEL_ID,
        input_digest="malformed-digest",
        status="pending",
    )
    db.add(analysis)
    db.commit()

    malformed_json = {"not_a_valid_schema": True, "error": "unknown"}

    from app.services import analysis as analysis_svc
    with patch.object(analysis_svc, "_call_nvidia", return_value=(malformed_json, 250, {})):
        analysis_svc.run_analysis(run, analysis, db)

    db.refresh(analysis)
    db.refresh(run)

    assert analysis.status == "failed"
    assert analysis.safe_error_code == "invalid_model_response"
    assert run.analysis_outcome == AnalysisOutcome.INVALID_RESPONSE.value
    assert run.test_outcome == TestOutcome.FAILED.value  # Authoritative test outcome preserved


# ──────────────────────────────────────────────────────────────────────────────
# 6. Opt-In Live Inference Test
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.live_provider
@pytest.mark.skipif(
    not LIVE_NVIDIA_ENABLED,
    reason="Remote live NVIDIA API test requires valid NVIDIA_API_KEY in environment",
)
def test_nvidia_live_handshake_and_schema_validation(
    db: Session, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "NVIDIA_TIMEOUT_SECONDS", 120)
    """Execute real live call against https://integrate.api.nvidia.com/v1 and verify schema."""
    uid = uuid.uuid4().int % 10000000
    user = User(github_user_id=8899 + uid, login=f"nvidia-live-tester-{uid}", display_name="Live Tester")
    db.add(user)
    db.commit()
    db.refresh(user)

    project = Project(
        owner_user_id=user.id,
        github_installation_id=98899 + uid,
        repository_id=98899 + uid,
        repository_full_name=f"{user.login}/nvidia-live-repo",
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    run = Run(
        project_id=project.id,
        head_sha="e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0e0",
        correlation_id=f"corr-live-{uid}",
        event_kind="push",
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.FAILED.value,
        analysis_outcome=AnalysisOutcome.QUEUED.value,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    live_content = "NullPointerException in UserAuth.java:42 during authentication check"
    evidence_item = EvidenceItem(
        run_id=run.id,
        kind=EvidenceKind.FAILED_ASSERTION.value,
        source_label="tests/test_auth.py",
        content_redacted=live_content,
        content_digest=hashlib.sha256(live_content.encode()).hexdigest(),
        truncated=False,
        byte_length=len(live_content.encode()),
    )
    db.add(evidence_item)
    db.commit()
    db.refresh(evidence_item)

    analysis = Analysis(
        run_id=run.id,
        attempt_number=1,
        provider="nvidia",
        model_id=settings.NVIDIA_MODEL_ID,
        input_digest="live-handshake-digest",
        status="pending",
    )
    db.add(analysis)
    db.commit()

    with caplog.at_level(logging.DEBUG):
        run_analysis(run, analysis, db)

    db.refresh(analysis)
    db.refresh(run)

    # Assertions on live run
    assert analysis.status == "succeeded", f"Live analysis failed with code: {analysis.safe_error_code}"
    assert analysis.provider == "nvidia"
    assert analysis.model_id == settings.NVIDIA_MODEL_ID
    assert run.analysis_outcome == AnalysisOutcome.SUCCEEDED.value
    assert run.test_outcome == TestOutcome.FAILED.value

    # Performance metrics
    assert analysis.latency_ms is not None and analysis.latency_ms > 0
    assert analysis.input_tokens is not None and analysis.input_tokens > 0
    assert analysis.output_tokens is not None and analysis.output_tokens > 0

    # Schema validation
    assert analysis.result_json is not None
    diagnosis = ModelDiagnosisSchema.model_validate(analysis.result_json)
    assert diagnosis.summary
    assert len(diagnosis.root_cause_hypotheses) > 0

    # Evidence IDs validation
    valid_ids = {str(evidence_item.id)}
    for hyp in diagnosis.root_cause_hypotheses:
        assert hyp.confidence in ("low", "medium", "high")
        for eid in hyp.evidence_ids:
            assert eid in valid_ids

    # Secret isolation check: API key must NEVER appear in logs
    for record in caplog.records:
        assert settings.NVIDIA_API_KEY not in record.message


# ──────────────────────────────────────────────────────────────────────────────
# 7. Security Hardening, Provider Precedence & Endpoint Isolation Tests
# ──────────────────────────────────────────────────────────────────────────────

def test_effective_ai_provider_precedence(monkeypatch):
    """Verify explicit provider precedence and fallback behavior."""
    # Explicit provider selection takes highest precedence
    monkeypatch.setattr(settings, "AI_PROVIDER", "nvidia")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nv-key")
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "neb-key")
    assert settings.effective_ai_provider == "nvidia"

    monkeypatch.setattr(settings, "AI_PROVIDER", "nebius")
    assert settings.effective_ai_provider == "nebius_token_factory"

    monkeypatch.setattr(settings, "AI_PROVIDER", "nebius_token_factory")
    assert settings.effective_ai_provider == "nebius_token_factory"

    monkeypatch.setattr(settings, "AI_PROVIDER", "nvidia_nim")
    assert settings.effective_ai_provider == "nvidia"

    # Implicit selection based on which key is configured
    monkeypatch.setattr(settings, "AI_PROVIDER", "")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "nv-key")
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "")
    assert settings.effective_ai_provider == "nvidia"

    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "neb-key")
    assert settings.effective_ai_provider == "nebius_token_factory"

    # Default fallback when neither key is present matches documented reference architecture
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "")
    assert settings.effective_ai_provider == "nebius_token_factory"


def test_synthetic_api_key_never_leaked_in_logs_or_errors(monkeypatch, caplog):
    """Verify that synthetic test keys are strictly isolated from logs, errors, and returned payloads."""
    synthetic_key = "nvapi-synthetic-test-key-never-leak-999"
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", synthetic_key)
    monkeypatch.setattr(settings, "NVIDIA_MAX_RETRIES", 0)

    def mock_post(*args, **kwargs):
        return httpx.Response(
            status_code=401,
            json={"error": {"message": "Invalid API Key provided", "type": "auth_error"}},
            request=httpx.Request("POST", "http://test"),
        )

    with caplog.at_level(logging.DEBUG), patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nvidia("diagnostic test prompt")

    raw, latency, usage = result
    assert raw is None
    assert result.error_code == "provider_auth_error"

    for record in caplog.records:
        assert synthetic_key not in record.message
        assert synthetic_key not in repr(record)


def test_nvidia_rate_limit_429_retries_with_backoff(monkeypatch):
    """Verify 429 rate limit is treated as transient and retries with backoff."""
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "dummy-key")
    monkeypatch.setattr(settings, "NVIDIA_MAX_RETRIES", 2)

    call_count = 0
    slept_delays = []

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=429,
            headers={"Retry-After": "1"},
            request=httpx.Request("POST", "http://test"),
        )

    from app.services import analysis as analysis_svc
    monkeypatch.setattr(analysis_svc, "_backoff_sleep", lambda s: slept_delays.append(s))

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nvidia("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 3
    assert len(slept_delays) == 2
    assert result.is_transient is True
    assert result.error_code == "provider_rate_limited"


@pytest.mark.asyncio
async def test_health_endpoints_do_not_expose_credentials(client: AsyncClient):
    """Health and metrics probes must respond successfully and never reveal credentials, providers, or secret keys."""
    resp_live = await client.get("/api/v1/health/live")
    assert resp_live.status_code == 200
    assert "nvidia" not in resp_live.text.lower()
    assert "nebius" not in resp_live.text.lower()
    assert "key" not in resp_live.text.lower()

    resp_ready = await client.get("/api/v1/health/ready")
    assert resp_ready.status_code == 200
    data = resp_ready.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert "nvidia" not in resp_ready.text.lower()
    assert "secret" not in resp_ready.text.lower()

    resp_metrics = await client.get("/metrics")
    assert resp_metrics.status_code == 200
    assert "nvapi-" not in resp_metrics.text
    assert settings.NVIDIA_API_KEY not in resp_metrics.text if settings.NVIDIA_API_KEY else True


@pytest.mark.asyncio
async def test_unauthenticated_request_cannot_trigger_analysis(
    client: AsyncClient,
    run_with_evidence: tuple[Run, list[EvidenceItem]],
):
    """Triggering analysis without valid session/token must be rejected with 401/403."""
    run, evidence = run_with_evidence
    response = await client.post(f"/api/v1/runs/{run.id}/analysis/request")
    assert response.status_code in (401, 403)
    error_data = response.json()
    assert "error" in error_data
    assert error_data["error"]["code"] in ("not_authenticated", "unauthorized", "forbidden")
