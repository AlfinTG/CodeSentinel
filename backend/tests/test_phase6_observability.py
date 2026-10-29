"""Tests for Phase 6: Telemetry, Observability, and Structured Logging.

Verifies:
- Prometheus /metrics endpoint format and headers
- HTTP request counter and latency histogram instrumentation
- Cardinality bounding via route templates and exclusion of /metrics
- Correlation and Request ID sanitization against CRLF injection
- Check duration, queue job, and Nebius token usage recording
- Circuit breaker state metric integration
- Active runs gauge transitions across lifecycle states
- Structured JSON log formatting with context-aware correlation tracking
- Safe handling of non-string and malformed inputs in redaction and logging
- Automatic secret and canary redaction within log messages and stack traces
"""

import json
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.logging import (
    StructuredJsonFormatter,
    clear_log_context,
    sanitize_correlation_id,
    set_log_context,
)
from app.core.telemetry import (
    ACTIVE_RUNS,
    dec_active_runs,
    generate_metrics_payload,
    inc_active_runs,
    record_check_execution,
    record_nebius_usage,
    record_queue_job_event,
    set_active_runs,
    set_circuit_breaker_metric,
)
from app.main import app
from app.models.run import LifecycleStatus
from app.services.redaction import redact_secrets
from app.services.transitions import transition_lifecycle


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_format():
    """Verify GET /metrics returns HTTP 200 with Prometheus text exposition."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")
        body = resp.text
        assert "codesentinel_http_requests_total" in body
        assert "codesentinel_http_request_duration_seconds" in body
        assert "codesentinel_circuit_breaker_state" in body


@pytest.mark.asyncio
async def test_http_request_instrumentation_records_metrics():
    """Verify HTTP requests through middleware increment metrics."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Request health check
        health_resp = await client.get("/api/v1/health/live")
        assert health_resp.status_code == 200
        assert "X-Request-ID" in health_resp.headers

        # Verify metrics updated with matched route template
        metrics_resp = await client.get("/metrics")
        assert metrics_resp.status_code == 200
        body = metrics_resp.text
        assert 'endpoint="/api/v1/health/live"' in body
        assert 'status_code="200"' in body


@pytest.mark.asyncio
async def test_unmatched_route_bounds_metric_cardinality():
    """Verify non-existent paths are labeled as 'unmatched' to prevent cardinality explosion."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        bad_resp = await client.get("/nonexistent/random-id-12345/arbitrary-path")
        assert bad_resp.status_code == 404

        metrics_resp = await client.get("/metrics")
        body = metrics_resp.text
        assert 'endpoint="unmatched"' in body
        assert "random-id-12345" not in body


@pytest.mark.asyncio
async def test_metrics_endpoint_does_not_self_instrument():
    """Verify scraping /metrics does not recursively increment HTTP request metrics."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Scrape /metrics multiple times
        await client.get("/metrics")
        metrics_resp = await client.get("/metrics")
        body = metrics_resp.text
        # Assert /metrics is not recorded as an endpoint label
        assert 'endpoint="/metrics"' not in body


@pytest.mark.asyncio
async def test_crlf_header_injection_sanitization():
    """Verify client-supplied request IDs containing CRLF or control chars are rejected and sanitized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        malicious_id = "test-id\r\nInjected-Header: evil"
        resp = await client.get("/api/v1/health/live", headers={"X-Request-ID": malicious_id})
        assert resp.status_code == 200
        returned_id = resp.headers["X-Request-ID"]
        assert "\r" not in returned_id
        assert "\n" not in returned_id
        assert "evil" not in returned_id


def test_sanitize_correlation_id_utility():
    """Test correlation ID sanitization utility directly."""
    assert sanitize_correlation_id("valid-id_123.45:abc", "fallback") == "valid-id_123.45:abc"
    assert sanitize_correlation_id("invalid\nid", "fallback") == "fallback"
    assert sanitize_correlation_id("id with spaces", "fallback") == "fallback"
    assert sanitize_correlation_id("a" * 200, "fallback") == "fallback"
    assert sanitize_correlation_id(None, "fallback") == "fallback"


def test_telemetry_check_execution_recording():
    """Verify check execution observations are recorded in telemetry."""
    record_check_execution(tool="pytest", outcome="passed", duration_seconds=1.45)
    payload, _ = generate_metrics_payload()
    text = payload.decode("utf-8")
    assert 'codesentinel_check_execution_duration_seconds_count{outcome="passed",tool="pytest"}' in text


def test_telemetry_queue_job_recording():
    """Verify queue job transitions and runtimes are recorded in telemetry."""
    record_queue_job_event(job_type="scan", status="enqueued")
    record_queue_job_event(job_type="scan", status="succeeded", duration_seconds=5.2)

    payload, _ = generate_metrics_payload()
    text = payload.decode("utf-8")
    assert 'codesentinel_queue_jobs_total{job_type="scan",status="enqueued"}' in text
    assert 'codesentinel_queue_jobs_total{job_type="scan",status="succeeded"}' in text
    assert 'codesentinel_queue_job_duration_seconds_count{job_type="scan",status="succeeded"}' in text


def test_telemetry_nebius_usage_recording():
    """Verify Nebius token usage and call latency are recorded in telemetry."""
    record_nebius_usage(
        model_id="nvidia/llama-3.1-nemotron-70b-instruct",
        prompt_tokens=450,
        completion_tokens=180,
        duration_seconds=2.85,
        outcome="succeeded",
    )

    payload, _ = generate_metrics_payload()
    text = payload.decode("utf-8")
    assert 'codesentinel_nebius_tokens_total{model_id="nvidia/llama-3.1-nemotron-70b-instruct",token_type="prompt"}' in text
    assert 'codesentinel_nebius_tokens_total{model_id="nvidia/llama-3.1-nemotron-70b-instruct",token_type="completion"}' in text
    assert 'codesentinel_nebius_call_duration_seconds_count{model_id="nvidia/llama-3.1-nemotron-70b-instruct",outcome="succeeded"}' in text


def test_circuit_breaker_gauge_metric():
    """Verify circuit breaker state gauge reflects CLOSED (0), HALF_OPEN (1), OPEN (2)."""
    set_circuit_breaker_metric("nebius", 2)
    payload, _ = generate_metrics_payload()
    text = payload.decode("utf-8")
    assert 'codesentinel_circuit_breaker_state{provider="nebius"} 2.0' in text

    set_circuit_breaker_metric("nebius", 0)
    payload, _ = generate_metrics_payload()
    text = payload.decode("utf-8")
    assert 'codesentinel_circuit_breaker_state{provider="nebius"} 0.0' in text


def test_active_runs_gauge_lifecycle_transitions():
    """Verify active runs gauge increments on active states and decrements on terminal states."""
    set_active_runs(0)
    assert ACTIVE_RUNS._value.get() == 0

    inc_active_runs()
    assert ACTIVE_RUNS._value.get() == 1

    dec_active_runs()
    assert ACTIVE_RUNS._value.get() == 0

    # Ensure it never goes negative
    dec_active_runs()
    assert ACTIVE_RUNS._value.get() >= 0

    # Test via transition_lifecycle
    set_active_runs(1)
    # Transition RUNNING -> COMPLETED (terminal) should decrement
    transition_lifecycle(LifecycleStatus.RUNNING, LifecycleStatus.COMPLETED)
    assert ACTIVE_RUNS._value.get() == 0


def test_redact_secrets_safe_with_non_string_and_malformed_inputs():
    """Verify redact_secrets handles non-string, null, or malformed inputs without raising."""
    assert redact_secrets(None) == ""
    assert redact_secrets(12345) == "12345"
    assert redact_secrets(["item1", "item2"]) == "['item1', 'item2']"

    # Redacts canary even when input was converted from object representation
    canary = "custom_canary_super_secret_val"
    res = redact_secrets(f"obj with {canary}", custom_canaries=[canary])
    assert canary not in res
    assert "[REDACTED_CANARY]" in res


def test_structured_json_formatter_basic_log():
    """Verify StructuredJsonFormatter outputs single-line JSON with required fields."""
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Execution started for job %s",
        args=("job-123",),
        exc_info=None,
    )

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.logger"
    assert parsed["message"] == "Execution started for job job-123"
    assert "timestamp" in parsed


def test_structured_json_formatter_never_crashes_on_bad_formatting():
    """Verify StructuredJsonFormatter returns safe fallback JSON when record formatting fails."""
    formatter = StructuredJsonFormatter()
    # Mismatched args for %d format
    record = logging.LogRecord(
        name="test.broken",
        level=logging.ERROR,
        pathname="test.py",
        lineno=10,
        msg="Expected int %d but got string",
        args=("not-an-int",),
        exc_info=None,
    )

    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] in ("ERROR", "INFO")
    assert "Expected int" in parsed["message"] or "[LOGGING_ERROR]" in parsed["message"]


def test_structured_json_formatter_context_propagation():
    """Verify ContextVar correlation identifiers are automatically embedded in log output."""
    formatter = StructuredJsonFormatter()
    clear_log_context()

    set_log_context(
        request_id="req-abc-123",
        correlation_id="corr-xyz-789",
        run_id="run-456",
    )

    record = logging.LogRecord(
        name="app.service",
        level=logging.WARNING,
        pathname="service.py",
        lineno=42,
        msg="Transient failure detected",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert parsed["request_id"] == "req-abc-123"
    assert parsed["correlation_id"] == "corr-xyz-789"
    assert parsed["run_id"] == "run-456"
    assert parsed["message"] == "Transient failure detected"

    clear_log_context()


def test_structured_json_formatter_secret_redaction():
    """Verify credentials and API keys in log messages are redacted before emission."""
    formatter = StructuredJsonFormatter(redact=True)
    clear_log_context()

    secret_msg = "Failed auth using key sk-1234567890abcdef123456 and token ghp_1234567890abcdef1234567890abcdef1234"
    record = logging.LogRecord(
        name="app.auth",
        level=logging.ERROR,
        pathname="auth.py",
        lineno=99,
        msg=secret_msg,
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert "sk-1234567890abcdef123456" not in parsed["message"]
    assert "[REDACTED_API_KEY]" in parsed["message"]
    assert "ghp_1234567890abcdef1234567890abcdef1234" not in parsed["message"]
    assert "[REDACTED_GITHUB_TOKEN]" in parsed["message"]


def test_structured_json_formatter_exception_traceback():
    """Verify exception stack traces are formatted and sanitized in JSON logs."""
    formatter = StructuredJsonFormatter(redact=True)

    try:
        raise ValueError("Database connection failed with password: secret_password_123")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="app.db",
            level=logging.ERROR,
            pathname="db.py",
            lineno=50,
            msg="Unhandled database error",
            args=(),
            exc_info=sys.exc_info(),
        )

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]
    assert "secret_password_123" not in parsed["exception"]
    assert "[REDACTED]" in parsed["exception"]
