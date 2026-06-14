"""Module with mixed suppression quality for testing."""

from typing import Any

x: str = 42  # type: ignore

values: list[int] = ["a", "b"]  # type: ignore

import os  # noqa

# This one is properly justified and should be allowed:
config: Any = load_settings()  # type: ignore[name-defined]  # mypy-bug: dynamic import not visible to mypy
