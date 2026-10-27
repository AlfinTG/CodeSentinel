"""Baseline test passing deterministically."""

from discount_calculator import compute_discount


def test_baseline_discount():
    assert compute_discount(100.0, 0.2) == 80.0
