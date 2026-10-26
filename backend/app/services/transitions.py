"""State transition machine and legal transition validations."""

from app.models.run import AnalysisOutcome, LifecycleStatus, TestOutcome


class InvalidStateTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(self, from_state: str, to_state: str, entity_name: str):
        super().__init__(
            f"Illegal {entity_name} state transition from '{from_state}' to '{to_state}'"
        )
        self.from_state = from_state
        self.to_state = to_state
        self.entity_name = entity_name


# Legal Lifecycle transitions
LEGAL_LIFECYCLE_TRANSITIONS: dict[LifecycleStatus, set[LifecycleStatus]] = {
    LifecycleStatus.RECEIVED: {
        LifecycleStatus.QUEUED,
        LifecycleStatus.CANCELLED,
        LifecycleStatus.FAILED,
    },
    LifecycleStatus.QUEUED: {
        LifecycleStatus.RUNNING,
        LifecycleStatus.CANCELLED,
        LifecycleStatus.TIMED_OUT,
        LifecycleStatus.INCONCLUSIVE,
        LifecycleStatus.FAILED,
    },
    LifecycleStatus.RUNNING: {
        LifecycleStatus.COMPLETED,
        LifecycleStatus.FAILED,
        LifecycleStatus.TIMED_OUT,
        LifecycleStatus.CANCELLED,
        LifecycleStatus.INCONCLUSIVE,
    },
    # Terminal states (cannot transition further except idempotent self-assignment)
    LifecycleStatus.COMPLETED: set(),
    LifecycleStatus.FAILED: set(),
    LifecycleStatus.TIMED_OUT: set(),
    LifecycleStatus.CANCELLED: set(),
    LifecycleStatus.INCONCLUSIVE: set(),
}

# Legal Test Outcome transitions
LEGAL_TEST_OUTCOME_TRANSITIONS: dict[TestOutcome, set[TestOutcome]] = {
    TestOutcome.PENDING: {
        TestOutcome.PASSED,
        TestOutcome.FAILED,
        TestOutcome.TIMED_OUT,
        TestOutcome.CANCELLED,
        TestOutcome.INCONCLUSIVE,
        TestOutcome.ERROR,
    },
    # Terminal test states
    TestOutcome.PASSED: set(),
    TestOutcome.FAILED: set(),
    TestOutcome.TIMED_OUT: set(),
    TestOutcome.CANCELLED: set(),
    TestOutcome.INCONCLUSIVE: set(),
    TestOutcome.ERROR: set(),
}

# Legal Analysis Outcome transitions (Completely independent from Test Outcomes)
LEGAL_ANALYSIS_TRANSITIONS: dict[AnalysisOutcome, set[AnalysisOutcome]] = {
    AnalysisOutcome.NOT_REQUESTED: {
        AnalysisOutcome.QUEUED,
    },
    AnalysisOutcome.QUEUED: {
        AnalysisOutcome.RUNNING,
        AnalysisOutcome.UNAVAILABLE,
    },
    AnalysisOutcome.RUNNING: {
        AnalysisOutcome.SUCCEEDED,
        AnalysisOutcome.UNAVAILABLE,
        AnalysisOutcome.INVALID_RESPONSE,
    },
    # Retryable / terminal analysis states
    AnalysisOutcome.SUCCEEDED: {
        AnalysisOutcome.QUEUED,  # Retries allowed if policy permits
    },
    AnalysisOutcome.UNAVAILABLE: {
        AnalysisOutcome.QUEUED,
    },
    AnalysisOutcome.INVALID_RESPONSE: {
        AnalysisOutcome.QUEUED,
    },
}


def transition_lifecycle(current: LifecycleStatus, target: LifecycleStatus) -> LifecycleStatus:
    """Validate and transition lifecycle status.

    Returns the new state or the current state if idempotent.
    """
    if current == target:
        return current  # Idempotent re-affirmation

    allowed = LEGAL_LIFECYCLE_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(current.value, target.value, "Lifecycle")
    return target


def transition_test_outcome(current: TestOutcome, target: TestOutcome) -> TestOutcome:
    """Validate and transition deterministic test outcome."""
    if current == target:
        return current

    allowed = LEGAL_TEST_OUTCOME_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(current.value, target.value, "TestOutcome")
    return target


def transition_analysis_outcome(
    current: AnalysisOutcome, target: AnalysisOutcome
) -> AnalysisOutcome:
    """Validate and transition AI diagnosis status."""
    if current == target:
        return current

    allowed = LEGAL_ANALYSIS_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise InvalidStateTransitionError(current.value, target.value, "AnalysisOutcome")
    return target
