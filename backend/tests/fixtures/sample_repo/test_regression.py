"""Deliberate regression test failing deterministically."""

from discount_calculator import compute_discount


def test_regression_case():
    # Deliberate regression assertion to generate reproducible failure evidence
    assert compute_discount(100.0, 0.5) == 20.0, "Regression detected: expected 20.0 after discount"
