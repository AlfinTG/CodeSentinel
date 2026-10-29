"""Prometheus telemetry registry and metrics instrumentation for CodeSentinel."""

import contextlib

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Dedicated registry to ensure isolation and prevent collision
registry = CollectorRegistry(auto_describe=True)

# --- HTTP Request Metrics ---
HTTP_REQUESTS_TOTAL = Counter(
    "codesentinel_http_requests_total",
    "Total HTTP requests received by the API layer",
    ["method", "endpoint", "status_code"],
    registry=registry,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "codesentinel_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)

# --- Run & Check Lifecycle Metrics ---
ACTIVE_RUNS = Gauge(
    "codesentinel_active_runs",
    "Number of runs currently active (in received, queued, or running lifecycle status)",
    registry=registry,
)

CHECK_EXECUTION_DURATION_SECONDS = Histogram(
    "codesentinel_check_execution_duration_seconds",
    "Duration of individual test/check executions in seconds",
    ["tool", "outcome"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
    registry=registry,
)

# --- Distributed Queue & Worker Metrics ---
QUEUE_JOBS_TOTAL = Counter(
    "codesentinel_queue_jobs_total",
    "Total background queue jobs handled by state transition",
    ["job_type", "status"],
    registry=registry,
)

QUEUE_JOB_DURATION_SECONDS = Histogram(
    "codesentinel_queue_job_duration_seconds",
    "Duration of queue job execution from claim to terminal status in seconds",
    ["job_type", "status"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
    registry=registry,
)

QUEUE_DEPTH = Gauge(
    "codesentinel_queue_depth",
    "Current number of pending/queued jobs awaiting worker claiming",
    ["job_type"],
    registry=registry,
)

# --- Nebius AI Analysis & Token Telemetry ---
NEBIUS_TOKENS_TOTAL = Counter(
    "codesentinel_nebius_tokens_total",
    "Total tokens consumed by Nebius Token Factory AI model invocations",
    ["model_id", "token_type"],
    registry=registry,
)

NEBIUS_CALL_DURATION_SECONDS = Histogram(
    "codesentinel_nebius_call_duration_seconds",
    "Duration of external calls to Nebius Token Factory in seconds",
    ["model_id", "outcome"],
    buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 45.0, 60.0),
    registry=registry,
)

CIRCUIT_BREAKER_STATE = Gauge(
    "codesentinel_circuit_breaker_state",
    "Current state of the Nebius circuit breaker (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
    ["provider"],
    registry=registry,
)
# Initialize provider circuit breaker gauge to CLOSED (0)
CIRCUIT_BREAKER_STATE.labels(provider="nebius").set(0)


# --- Helper Instrumentation Functions ---

def record_http_request(method: str, endpoint: str, status_code: int, duration_seconds: float) -> None:
    """Record an HTTP request count and latency observation."""
    with contextlib.suppress(Exception):
        HTTP_REQUESTS_TOTAL.labels(
            method=method.upper(),
            endpoint=endpoint,
            status_code=str(status_code),
        ).inc()
        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=method.upper(),
            endpoint=endpoint,
        ).observe(duration_seconds)


def record_check_execution(tool: str, outcome: str, duration_seconds: float) -> None:
    """Record a check execution observation."""
    with contextlib.suppress(Exception):
        CHECK_EXECUTION_DURATION_SECONDS.labels(
            tool=tool,
            outcome=outcome,
        ).observe(duration_seconds)


def record_queue_job_event(job_type: str, status: str, duration_seconds: float | None = None) -> None:
    """Record a queue job transition and optional runtime."""
    with contextlib.suppress(Exception):
        QUEUE_JOBS_TOTAL.labels(
            job_type=job_type,
            status=status,
        ).inc()
        if duration_seconds is not None and duration_seconds >= 0:
            QUEUE_JOB_DURATION_SECONDS.labels(
                job_type=job_type,
                status=status,
            ).observe(duration_seconds)


def record_nebius_usage(
    model_id: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_seconds: float = 0.0,
    outcome: str = "succeeded",
) -> None:
    """Record Nebius Token Factory token usage and call latency."""
    with contextlib.suppress(Exception):
        if prompt_tokens > 0:
            NEBIUS_TOKENS_TOTAL.labels(model_id=model_id, token_type="prompt").inc(prompt_tokens)
        if completion_tokens > 0:
            NEBIUS_TOKENS_TOTAL.labels(model_id=model_id, token_type="completion").inc(completion_tokens)
        if duration_seconds > 0:
            NEBIUS_CALL_DURATION_SECONDS.labels(model_id=model_id, outcome=outcome).observe(duration_seconds)


def set_circuit_breaker_metric(provider: str, state_value: int) -> None:
    """Update circuit breaker gauge state: 0=CLOSED, 1=HALF_OPEN, 2=OPEN."""
    with contextlib.suppress(Exception):
        CIRCUIT_BREAKER_STATE.labels(provider=provider).set(state_value)


def inc_active_runs() -> None:
    """Increment active runs gauge."""
    with contextlib.suppress(Exception):
        ACTIVE_RUNS.inc()


def dec_active_runs() -> None:
    """Decrement active runs gauge safely without going negative."""
    with contextlib.suppress(Exception):
        val = getattr(ACTIVE_RUNS, "_value", None)
        if val is not None:
            current = val.get()
            if current > 0:
                ACTIVE_RUNS.dec()
        else:
            ACTIVE_RUNS.dec()


def set_active_runs(count: int) -> None:
    """Explicitly set active runs gauge count."""
    with contextlib.suppress(Exception):
        ACTIVE_RUNS.set(max(0, count))


def generate_metrics_payload() -> tuple[bytes, str]:
    """Generate the latest Prometheus text exposition bytes and content type."""
    return generate_latest(registry), CONTENT_TYPE_LATEST
