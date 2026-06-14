"""Tests for clean_module."""

from src.clean_module import add, clamp


def test_add():
    """Verify basic addition."""
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_clamp():
    """Verify value clamping to range."""
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(15, 0, 10) == 10
