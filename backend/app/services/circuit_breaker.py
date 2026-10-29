"""Circuit breaker pattern implementation for external service resiliency.

Protects upstream providers (such as Nebius Token Factory) from being spammed
during outages, rate limits, or consecutive server errors, while allowing
fast recovery through trial/probe calls in half-open state.

States:
- CLOSED: Normal operation. Requests flow through.
- OPEN: Circuit tripped due to consecutive failures exceeding threshold.
        Requests are rejected immediately without network I/O.
- HALF_OPEN: Recovery timeout elapsed. Exactly one recovery probe is permitted
             in flight at a time. If successful, circuit resets to CLOSED.
             If it fails, circuit returns to OPEN. Concurrent calls while a probe
             is in flight are rejected until the probe resolves.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, enum.Enum):
    """Possible states for the circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when an operation is attempted while the circuit is OPEN or probe in flight."""

    def __init__(self, message: str = "Circuit breaker is OPEN", name: str = ""):
        super().__init__(message)
        self.name = name


class CircuitBreaker:
    """Thread-safe circuit breaker with failure thresholds and serialized half-open recovery probes."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 30.0,
        name: str = "default",
    ) -> None:
        """Initialize circuit breaker.

        Args:
            failure_threshold: Consecutive failures before tripping to OPEN.
            recovery_timeout_seconds: Seconds to stay OPEN before allowing a probe in HALF_OPEN.
            name: Identifier for logging and diagnostics.
        """
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout_seconds < 0:
            raise ValueError("recovery_timeout_seconds must be >= 0")

        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.name = name

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_state_change: float = time.monotonic()
        self._last_failure_time: float | None = None
        self._half_open_probes_inflight: int = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """Current state of the circuit breaker, evaluating timed recovery if needed."""
        with self._lock:
            self._evaluate_state()
            return self._state

    @property
    def consecutive_failures(self) -> int:
        """Current count of consecutive failures."""
        with self._lock:
            return self._consecutive_failures

    @property
    def probe_in_flight(self) -> bool:
        """Whether a recovery probe is currently in flight in HALF_OPEN."""
        with self._lock:
            return self._half_open_probes_inflight > 0

    def _evaluate_state(self) -> None:
        """Internal helper to transition OPEN to HALF_OPEN when recovery time has elapsed.

        Must be called while holding self._lock.
        """
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_probes_inflight = 0
                self._last_state_change = time.monotonic()
                logger.info(
                    "Circuit breaker [%s] recovery timeout elapsed (%.1fs); entering HALF_OPEN state",
                    self.name,
                    elapsed,
                )

    def can_execute(self) -> bool:
        """Check whether a request is permitted to proceed.

        Returns:
            True if the circuit is CLOSED.
            True if the circuit is HALF_OPEN and no probe is currently in flight
                 (atomically reserving the single allowed probe slot).
            False if the circuit is OPEN or if a probe is already in flight in HALF_OPEN.
        """
        with self._lock:
            self._evaluate_state()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_probes_inflight < 1:
                    self._half_open_probes_inflight = 1
                    logger.debug(
                        "Circuit breaker [%s] reserved HALF_OPEN recovery probe slot",
                        self.name,
                    )
                    return True
                logger.debug(
                    "Circuit breaker [%s] probe already in flight in HALF_OPEN; rejecting concurrent call",
                    self.name,
                )
                return False
            return False

    def record_success(self) -> None:
        """Record a successful execution.

        Resets consecutive failures to 0, clears any in-flight probe reservation,
        and transitions HALF_OPEN back to CLOSED.
        """
        with self._lock:
            self._half_open_probes_inflight = 0
            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                logger.info(
                    "Circuit breaker [%s] probe succeeded; resetting state to CLOSED",
                    self.name,
                )
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._last_state_change = time.monotonic()

    def record_failure(self) -> None:
        """Record a failed execution.

        Clears any in-flight probe reservation. If in HALF_OPEN, immediately trips
        back to OPEN. If in CLOSED and consecutive failures reaches threshold, trips to OPEN.
        """
        with self._lock:
            self._evaluate_state()
            self._half_open_probes_inflight = 0
            self._consecutive_failures += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._last_state_change = time.monotonic()
                logger.warning(
                    "Circuit breaker [%s] probe failed in HALF_OPEN; re-tripping to OPEN",
                    self.name,
                )
            elif self._state == CircuitState.CLOSED:
                if self._consecutive_failures >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._last_state_change = time.monotonic()
                    logger.warning(
                        "Circuit breaker [%s] TRIPPED to OPEN after %d consecutive failures "
                        "(threshold=%d, recovery_seconds=%.1f)",
                        self.name,
                        self._consecutive_failures,
                        self.failure_threshold,
                        self.recovery_timeout_seconds,
                    )

    def release_probe(self) -> None:
        """Release an in-flight probe reservation without recording success or failure.

        Use this in cleanup / exception paths where a probe was reserved by can_execute()
        but could not complete or record a definitive result.
        """
        with self._lock:
            if self._half_open_probes_inflight > 0:
                self._half_open_probes_inflight = 0
                logger.debug(
                    "Circuit breaker [%s] released in-flight probe reservation",
                    self.name,
                )

    def reset(self) -> None:
        """Forcefully reset circuit breaker to CLOSED with 0 failures and no active probes."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._half_open_probes_inflight = 0
            self._last_state_change = time.monotonic()
            self._last_failure_time = None
            logger.info("Circuit breaker [%s] manually reset to CLOSED", self.name)
