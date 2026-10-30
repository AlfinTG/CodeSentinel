"""Unit and integration tests for Phase 4 Circuit Breaker resiliency and error classification."""

import hashlib
import threading
import time
import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis import Analysis
from app.models.evidence import EvidenceItem
from app.models.project import Project
from app.models.run import AnalysisOutcome, LifecycleStatus, Run, TestOutcome
from app.models.user import User
from app.services import analysis as analysis_svc
from app.services.analysis import NebiusCallResult, _call_nebius, _compute_backoff_delay
from app.services.circuit_breaker import CircuitBreaker, CircuitState


@pytest.fixture(autouse=True)
def reset_global_circuit_breaker():
    """Ensure the global Nebius circuit breaker is clean before and after every test."""
    analysis_svc.nebius_circuit_breaker.reset()
    yield
    analysis_svc.nebius_circuit_breaker.reset()


@pytest.fixture
def user(db: Session) -> User:
    u = User(
        github_user_id=8888,
        login="circuit-user",
        display_name="Circuit User",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def project(db: Session, user: User) -> Project:
    p = Project(
        owner_user_id=user.id,
        github_installation_id=1001,
        repository_id=2001,
        repository_full_name="test-org/circuit-test",
        default_branch="main",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def run_with_evidence(db: Session, project: Project) -> tuple[Run, list[EvidenceItem]]:
    r = Run(
        project_id=project.id,
        head_sha="cb11112222333344445555666677778888999900",
        event_kind="check_suite",
        lifecycle_status=LifecycleStatus.COMPLETED.value,
        test_outcome=TestOutcome.FAILED.value,
        analysis_outcome=AnalysisOutcome.NOT_REQUESTED.value,
        correlation_id=str(uuid.uuid4()),
    )
    db.add(r)
    db.commit()
    db.refresh(r)

    content = "FAILED test_circuit.py: Assertion failed"
    items = [
        EvidenceItem(
            run_id=r.id,
            kind="failure_summary",
            source_label="pytest",
            content_redacted=content,
            content_digest=hashlib.sha256(content.encode()).hexdigest(),
            truncated=False,
            byte_length=len(content.encode()),
        )
    ]
    for it in items:
        db.add(it)
    db.commit()
    return r, items


# ==============================================================================
# Unit tests for CircuitBreaker standalone class
# ==============================================================================


def test_circuit_breaker_init_validation():
    """Circuit breaker rejects invalid thresholds."""
    with pytest.raises(ValueError, match="failure_threshold"):
        CircuitBreaker(failure_threshold=0)

    with pytest.raises(ValueError, match="recovery_timeout_seconds"):
        CircuitBreaker(recovery_timeout_seconds=-1.0)


def test_circuit_breaker_initial_state():
    """Circuit breaker starts in CLOSED state with 0 failures and no active probes."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=5.0, name="test")
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    assert cb.probe_in_flight is False
    assert cb.can_execute() is True


def test_circuit_breaker_success_resets_failure_count():
    """A success resets consecutive failures back to 0."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=5.0)
    cb.record_failure()
    assert cb.consecutive_failures == 1
    assert cb.state == CircuitState.CLOSED

    cb.record_success()
    assert cb.consecutive_failures == 0
    assert cb.state == CircuitState.CLOSED


def test_circuit_breaker_trips_to_open_at_threshold():
    """Consecutive failures reaching threshold trip the circuit to OPEN."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=10.0)

    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_failure()  # 3rd failure reaches threshold
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False


def test_circuit_breaker_transitions_to_half_open_after_timeout(monkeypatch):
    """Circuit transitions from OPEN to HALF_OPEN after recovery timeout.

    Uses deterministic clock progression via monkeypatch to eliminate wall-clock
    timing jitter and race conditions under heavy CI/runner CPU load.
    """
    simulated_time = 1000.0

    def mock_monotonic():
        return simulated_time

    monkeypatch.setattr(time, "monotonic", mock_monotonic)

    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=30.0)

    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Advance simulated time past the 30.0s recovery timeout deterministically
    simulated_time += 31.0

    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN


def test_circuit_breaker_half_open_single_probe_concurrency(monkeypatch):
    """In HALF_OPEN state, only ONE recovery probe is allowed in flight at a time."""
    simulated_time = 1000.0

    def mock_monotonic():
        return simulated_time

    monkeypatch.setattr(time, "monotonic", mock_monotonic)

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30.0)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Advance simulated time past recovery timeout deterministically
    simulated_time += 31.0
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.probe_in_flight is False

    # Caller 1 reserves the single allowed probe slot
    assert cb.can_execute() is True
    assert cb.probe_in_flight is True

    # Caller 2 and concurrent callers are rejected while Caller 1 probe is in flight
    assert cb.can_execute() is False
    assert cb.can_execute() is False

    # Multi-threaded concurrent attempts must all be rejected while probe is in flight
    results = []

    def try_call():
        results.append(cb.can_execute())

    threads = [threading.Thread(target=try_call) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r is False for r in results)

    # Caller 1 probe succeeds
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.probe_in_flight is False
    assert cb.consecutive_failures == 0

    # Subsequent callers now proceed normally in CLOSED state
    assert cb.can_execute() is True


def test_circuit_breaker_half_open_failure_retrips_to_open(monkeypatch):
    """A failed probe in HALF_OPEN immediately re-trips back to OPEN and clears probe slot."""
    simulated_time = 1000.0

    def mock_monotonic():
        return simulated_time

    monkeypatch.setattr(time, "monotonic", mock_monotonic)

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30.0)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Advance simulated time past recovery timeout deterministically
    simulated_time += 31.0
    assert cb.can_execute() is True
    assert cb.probe_in_flight is True

    # Failed probe
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.probe_in_flight is False
    assert cb.can_execute() is False


def test_circuit_breaker_release_probe_unblocks_subsequent_probe(monkeypatch):
    """release_probe() clears the probe slot without altering failure counts or state."""
    simulated_time = 1000.0

    def mock_monotonic():
        return simulated_time

    monkeypatch.setattr(time, "monotonic", mock_monotonic)

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30.0)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    # Advance simulated time past recovery timeout deterministically
    simulated_time += 31.0
    assert cb.can_execute() is True
    assert cb.probe_in_flight is True
    assert cb.can_execute() is False  # 2nd caller blocked

    # Release probe slot (e.g., due to client error or aborted request)
    cb.release_probe()
    assert cb.probe_in_flight is False

    # Another caller can now probe
    assert cb.can_execute() is True
    assert cb.probe_in_flight is True


def test_circuit_breaker_manual_reset():
    """Manual reset restores CLOSED state, clears probe slots, and resets failure count."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0
    assert cb.probe_in_flight is False
    assert cb.can_execute() is True


def test_circuit_breaker_thread_safety():
    """Circuit breaker state transitions are thread-safe under concurrency."""
    cb = CircuitBreaker(failure_threshold=100, recovery_timeout_seconds=5.0)

    def worker():
        for _ in range(50):
            cb.record_failure()
            cb.can_execute()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert cb.consecutive_failures == 200
    assert cb.state == CircuitState.OPEN


# ==============================================================================
# Unit tests for _call_nebius HTTP error classification and retries
# ==============================================================================


def test_call_nebius_permanent_401_auth_error_not_retried(monkeypatch):
    """HTTP 401 Unauthorized is classified as permanent client error and never retried."""
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "dummy-key")

    call_count = 0

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = httpx.Response(status_code=401, request=httpx.Request("POST", "http://test"))
        return resp

    sleep_mock = MagicMock()
    monkeypatch.setattr(analysis_svc, "_backoff_sleep", sleep_mock)

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nebius("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 1  # exactly 1 attempt - NO RETRY
    assert sleep_mock.call_count == 0
    assert result.error_code == "provider_auth_error"
    assert result.is_transient is False


def test_call_nebius_permanent_400_invalid_request_not_retried(monkeypatch):
    """HTTP 400 Bad Request is classified as permanent client error and not retried."""
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "dummy-key")

    call_count = 0

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=400, request=httpx.Request("POST", "http://test"))

    sleep_mock = MagicMock()
    monkeypatch.setattr(analysis_svc, "_backoff_sleep", sleep_mock)

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nebius("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 1  # exactly 1 attempt - NO RETRY
    assert sleep_mock.call_count == 0
    assert result.error_code == "provider_invalid_request"
    assert result.is_transient is False


def test_call_nebius_transient_429_retries_with_retry_after(monkeypatch):
    """HTTP 429 Rate Limit is classified as transient and retried respecting Retry-After."""
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "dummy-key")
    monkeypatch.setattr(settings, "NEBIUS_MAX_RETRIES", 2)

    call_count = 0

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        headers = {"Retry-After": "2"}
        return httpx.Response(
            status_code=429, headers=headers, request=httpx.Request("POST", "http://test")
        )

    slept_delays = []

    def mock_sleep(sec):
        slept_delays.append(sec)

    monkeypatch.setattr(analysis_svc, "_backoff_sleep", mock_sleep)

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nebius("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 3  # attempt 0 + 2 retries
    assert len(slept_delays) == 2
    assert slept_delays == [2.0, 2.0]  # respected Retry-After header
    assert result.error_code == "provider_unavailable"
    assert result.is_transient is True


def test_call_nebius_transient_500_server_error_retries_with_exponential_backoff(monkeypatch):
    """HTTP 500 Server Error is retried up to MAX_RETRIES with bounded exponential backoff."""
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "dummy-key")
    monkeypatch.setattr(settings, "NEBIUS_MAX_RETRIES", 2)

    call_count = 0

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return httpx.Response(status_code=500, request=httpx.Request("POST", "http://test"))

    slept_delays = []

    def mock_sleep(sec):
        slept_delays.append(sec)

    monkeypatch.setattr(analysis_svc, "_backoff_sleep", mock_sleep)

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nebius("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 3  # 1 initial + 2 retries
    assert len(slept_delays) == 2
    # Check that backoff was exponential and bounded (attempt 0 base 0.5s, attempt 1 base 1.0s)
    assert 0.4 <= slept_delays[0] <= 1.5
    assert 0.8 <= slept_delays[1] <= 2.5
    assert result.is_transient is True


def test_call_nebius_timeout_retries_and_marks_transient(monkeypatch):
    """Timeouts are retried up to MAX_RETRIES and classified as transient."""
    monkeypatch.setattr(settings, "NEBIUS_TOKEN_FACTORY_API_KEY", "dummy-key")
    monkeypatch.setattr(settings, "NEBIUS_MAX_RETRIES", 1)

    call_count = 0

    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise httpx.TimeoutException("Read timed out")

    sleep_mock = MagicMock()
    monkeypatch.setattr(analysis_svc, "_backoff_sleep", sleep_mock)

    with patch.object(httpx.Client, "post", side_effect=mock_post):
        result = _call_nebius("test prompt")

    raw, latency, usage = result
    assert raw is None
    assert call_count == 2
    assert sleep_mock.call_count == 1
    assert result.is_transient is True


def test_compute_backoff_delay_bounds():
    """_compute_backoff_delay produces bounded delays within safe limits."""
    # With valid Retry-After
    resp_429 = httpx.Response(429, headers={"Retry-After": "3.5"}, request=httpx.Request("POST", "http://test"))
    assert _compute_backoff_delay(0, resp_429) == 3.5

    # Excessively large Retry-After is ignored and falls back to bounded backoff
    resp_large = httpx.Response(429, headers={"Retry-After": "9999"}, request=httpx.Request("POST", "http://test"))
    delay_large = _compute_backoff_delay(0, resp_large)
    assert delay_large <= 5.0

    # Exponential increase bounded by 5.0s
    for attempt in range(5):
        d = _compute_backoff_delay(attempt)
        assert 0.1 <= d <= 5.0


# ==============================================================================
# Integration tests with run_analysis() pipeline
# ==============================================================================


def test_circuit_breaker_trips_in_analysis_pipeline(
    db: Session, run_with_evidence: tuple[Run, list[EvidenceItem]]
):
    """3 consecutive provider failures trip the breaker, causing 4th call to fast-fail."""
    run, evidence = run_with_evidence

    analysis_svc.nebius_circuit_breaker.failure_threshold = 3
    analysis_svc.nebius_circuit_breaker.recovery_timeout_seconds = 10.0

    # Simulate 3 provider failures
    for attempt in range(1, 4):
        analysis = Analysis(
            run_id=run.id,
            attempt_number=attempt,
            model_id=settings.NEBIUS_MODEL_ID,
            input_digest=f"digest-{attempt}",
            status="pending",
        )
        db.add(analysis)
        db.commit()

        with patch.object(analysis_svc, "_call_nebius", return_value=(None, 0, {})):
            analysis_svc.run_analysis(run, analysis, db)

        db.refresh(analysis)
        assert analysis.status == "failed"
        assert analysis.safe_error_code == "provider_unavailable"

    # Circuit breaker is now OPEN
    assert analysis_svc.nebius_circuit_breaker.state == CircuitState.OPEN
    assert analysis_svc.nebius_circuit_breaker.can_execute() is False

    # 4th call should fast-fail without calling _call_nebius at all
    analysis_4 = Analysis(
        run_id=run.id,
        attempt_number=4,
        model_id=settings.NEBIUS_MODEL_ID,
        input_digest="digest-4",
        status="pending",
    )
    db.add(analysis_4)
    db.commit()

    call_mock = MagicMock()
    with patch.object(analysis_svc, "_call_nebius", call_mock):
        analysis_svc.run_analysis(run, analysis_4, db)

    db.refresh(analysis_4)
    db.refresh(run)

    # _call_nebius must NOT have been called
    assert call_mock.call_count == 0
    assert analysis_4.status == "failed"
    assert analysis_4.safe_error_code == "circuit_breaker_open"
    assert run.analysis_outcome == AnalysisOutcome.UNAVAILABLE.value
    # Deterministic truth invariant: test outcome MUST remain FAILED
    assert run.test_outcome == TestOutcome.FAILED.value


def test_run_analysis_permanent_auth_error_does_not_trip_circuit_breaker(
    db: Session, run_with_evidence: tuple[Run, list[EvidenceItem]]
):
    """Permanent client errors (e.g. 401) set safe_error_code without incrementing CB failure count."""
    run, evidence = run_with_evidence

    analysis_svc.nebius_circuit_breaker.failure_threshold = 2
    analysis_svc.nebius_circuit_breaker.reset()

    auth_err_result = NebiusCallResult(
        None, 50, {}, error_code="provider_auth_error", is_transient=False
    )

    for attempt in range(1, 4):
        analysis = Analysis(
            run_id=run.id,
            attempt_number=attempt,
            model_id=settings.NEBIUS_MODEL_ID,
            input_digest=f"digest-auth-{attempt}",
            status="pending",
        )
        db.add(analysis)
        db.commit()

        with patch.object(analysis_svc, "_call_nebius", return_value=auth_err_result):
            analysis_svc.run_analysis(run, analysis, db)

        db.refresh(analysis)
        db.refresh(run)

        assert analysis.status == "failed"
        assert analysis.safe_error_code == "provider_auth_error"
        assert run.analysis_outcome == AnalysisOutcome.UNAVAILABLE.value
        # Deterministic truth invariant preserved
        assert run.test_outcome == TestOutcome.FAILED.value

    # Circuit breaker remains CLOSED because 401 is permanent client error, not transient provider outage
    assert analysis_svc.nebius_circuit_breaker.state == CircuitState.CLOSED
    assert analysis_svc.nebius_circuit_breaker.consecutive_failures == 0


def test_run_analysis_half_open_concurrent_probe_rejection(
    db: Session, run_with_evidence: tuple[Run, list[EvidenceItem]], monkeypatch
):
    """When in HALF_OPEN, concurrent runs are rejected while the recovery probe is active."""
    run, evidence = run_with_evidence
    evidence_id = str(evidence[0].id)

    simulated_time = [1000.0]

    def mock_monotonic():
        return simulated_time[0]

    monkeypatch.setattr(time, "monotonic", mock_monotonic)

    analysis_svc.nebius_circuit_breaker.failure_threshold = 1
    analysis_svc.nebius_circuit_breaker.recovery_timeout_seconds = 30.0

    # 1. Trip circuit breaker with 1 failure
    an1 = Analysis(
        run_id=run.id,
        attempt_number=1,
        model_id=settings.NEBIUS_MODEL_ID,
        input_digest="d-1",
        status="pending",
    )
    db.add(an1)
    db.commit()

    with patch.object(analysis_svc, "_call_nebius", return_value=(None, 0, {})):
        analysis_svc.run_analysis(run, an1, db)

    assert analysis_svc.nebius_circuit_breaker.state == CircuitState.OPEN

    # 2. Advance simulated time past recovery timeout deterministically
    simulated_time[0] += 31.0

    # 3. Create two pending analyses
    an2 = Analysis(
        run_id=run.id,
        attempt_number=2,
        model_id=settings.NEBIUS_MODEL_ID,
        input_digest="d-2",
        status="pending",
    )
    an3 = Analysis(
        run_id=run.id,
        attempt_number=3,
        model_id=settings.NEBIUS_MODEL_ID,
        input_digest="d-3",
        status="pending",
    )
    db.add_all([an2, an3])
    db.commit()

    valid_response = {
        "summary": "Root cause hypothesis",
        "root_cause_hypotheses": [
            {
                "statement": "Hypothesis: assertion failure",
                "evidence_ids": [evidence_id],
                "confidence": "high",
                "reasoning": "Observed failure in test trace",
            }
        ],
        "affected_behaviors": ["tests"],
        "recommended_next_steps": ["Check logic"],
        "missing_evidence": [],
    }

    # Simulate slow probe for run 2
    probe_started = threading.Event()
    probe_finish = threading.Event()

    def slow_call(*args, **kwargs):
        probe_started.set()
        probe_finish.wait(timeout=2.0)
        return valid_response, 120, {"prompt_tokens": 50, "completion_tokens": 20}

    # Thread 1 starts the probe
    def worker1():
        with patch.object(analysis_svc, "_call_nebius", side_effect=slow_call):
            analysis_svc.run_analysis(run, an2, db)

    t1 = threading.Thread(target=worker1)
    t1.start()

    probe_started.wait(timeout=2.0)
    assert analysis_svc.nebius_circuit_breaker.probe_in_flight is True

    # Concurrent Thread 2 attempts analysis while probe is in flight
    call_mock = MagicMock()
    with patch.object(analysis_svc, "_call_nebius", call_mock):
        analysis_svc.run_analysis(run, an3, db)

    db.refresh(an3)
    # Thread 2 was rejected without calling provider!
    assert call_mock.call_count == 0
    assert an3.status == "failed"
    assert an3.safe_error_code == "circuit_breaker_open"

    # Allow probe to complete
    probe_finish.set()
    t1.join(timeout=2.0)

    db.refresh(an2)
    assert an2.status == "succeeded"
    assert analysis_svc.nebius_circuit_breaker.state == CircuitState.CLOSED
    assert analysis_svc.nebius_circuit_breaker.probe_in_flight is False


def test_run_analysis_exception_releases_probe_reservation(
    db: Session, run_with_evidence: tuple[Run, list[EvidenceItem]], monkeypatch
):
    """An unexpected exception during probe execution releases probe slot so circuit does not freeze."""
    run, evidence = run_with_evidence

    simulated_time = [1000.0]

    def mock_monotonic():
        return simulated_time[0]

    monkeypatch.setattr(time, "monotonic", mock_monotonic)

    analysis_svc.nebius_circuit_breaker.failure_threshold = 1
    analysis_svc.nebius_circuit_breaker.recovery_timeout_seconds = 30.0
    analysis_svc.nebius_circuit_breaker.record_failure()
    assert analysis_svc.nebius_circuit_breaker.state == CircuitState.OPEN

    # Advance simulated time past recovery timeout deterministically
    simulated_time[0] += 31.0

    an = Analysis(
        run_id=run.id,
        attempt_number=1,
        model_id=settings.NEBIUS_MODEL_ID,
        input_digest="d-exc",
        status="pending",
    )
    db.add(an)
    db.commit()

    # Force unexpected exception inside _call_nebius
    with patch.object(analysis_svc, "_call_nebius", side_effect=RuntimeError("unexpected crash")):
        analysis_svc.run_analysis(run, an, db)

    db.refresh(an)
    assert an.status == "failed"
    assert an.safe_error_code == "internal_error"
    # Probe reservation must be released!
    assert analysis_svc.nebius_circuit_breaker.probe_in_flight is False
    # Next call can attempt probe without deadlock
    assert analysis_svc.nebius_circuit_breaker.can_execute() is True
