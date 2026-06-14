"""Utility functions for data transformation."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def clamp(value: float, low: float, high: float) -> float:
    """Constrain value to the range [low, high]."""
    if value < low:
        return low
    if value > high:
        return high
    return value
