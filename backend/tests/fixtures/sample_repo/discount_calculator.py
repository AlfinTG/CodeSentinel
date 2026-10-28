"""Sample discount calculator for controlled regression fixture."""


def compute_discount(price: float, discount_rate: float) -> float:
    """Calculate discounted price."""
    if discount_rate < 0 or discount_rate > 1:
        raise ValueError("Invalid discount rate")
    return price * (1 - discount_rate)
