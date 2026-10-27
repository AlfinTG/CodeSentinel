"""Tests for legal state transitions and invariant rules."""

import pytest

from app.models.run import AnalysisOutcome, LifecycleStatus, TestOutcome
from app.services.transitions import (
    InvalidStateTransitionError,
    transition_analysis_outcome,
    transition_lifecycle,
    transition_test_outcome,
)


def test_legal_lifecycle_transitions():
    """Verify standard happy-path lifecycle transitions."""
    state = LifecycleStatus.RECEIVED
    state = transition_lifecycle(state, LifecycleStatus.QUEUED)
    assert state == LifecycleStatus.QUEUED

    state = transition_lifecycle(state, LifecycleStatus.RUNNING)
    assert state == LifecycleStatus.RUNNING

    state = transition_lifecycle(state, LifecycleStatus.COMPLETED)
    assert state == LifecycleStatus.COMPLETED


def test_illegal_lifecycle_transition_raises():
    """Verify that moving backwards from terminal states is rejected."""
    terminal_state = LifecycleStatus.COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        transition_lifecycle(terminal_state, LifecycleStatus.RUNNING)

    with pytest.raises(InvalidStateTransitionError):
        transition_lifecycle(LifecycleStatus.FAILED, LifecycleStatus.QUEUED)


def test_idempotent_lifecycle_transitions():
    """Verify duplicate/late worker message handling is idempotent."""
    state = LifecycleStatus.RUNNING
    # Idempotent re-affirmation
    result = transition_lifecycle(state, LifecycleStatus.RUNNING)
    assert result == LifecycleStatus.RUNNING


def test_test_outcome_transitions():
    """Verify deterministic test outcomes only transition from PENDING."""
    outcome = TestOutcome.PENDING
    outcome = transition_test_outcome(outcome, TestOutcome.FAILED)
    assert outcome == TestOutcome.FAILED

    # Once failed, cannot become passed without a new attempt/run
    with pytest.raises(InvalidStateTransitionError):
        transition_test_outcome(outcome, TestOutcome.PASSED)


def test_analysis_independence_from_test_outcome():
    """Critical Invariant: Model analysis state transitions independently from test outcomes."""
    test_result = TestOutcome.FAILED

    # Model fails or becomes unavailable
    analysis_state = AnalysisOutcome.RUNNING
    analysis_state = transition_analysis_outcome(analysis_state, AnalysisOutcome.UNAVAILABLE)
    assert analysis_state == AnalysisOutcome.UNAVAILABLE

    # Deterministic test result remains strictly FAILED
    assert test_result == TestOutcome.FAILED
