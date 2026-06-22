"""Shared test infrastructure for hook tests.

Provides run_hook() and run_bash_hook() helpers (via hook_runner),
plus payload-building fixtures for the four tool types hooks commonly
receive (Read, Bash, Edit, Write).
"""

from typing import Any

import pytest

from tests.test_hooks.hook_runner import HOOKS_DIR, run_bash_hook, run_hook

__all__ = ["HOOKS_DIR", "run_hook", "run_bash_hook"]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark any test using Hypothesis's @given decorator."""
    hypothesis_marker = pytest.mark.hypothesis
    for item in items:
        if any(marker.name == "hypothesis" for marker in item.iter_markers()):
            continue
        obj = getattr(item, "obj", None)
        if obj is not None and getattr(obj, "is_hypothesis_test", False):
            item.add_marker(hypothesis_marker)


@pytest.fixture
def read_payload():
    def _make(file_path: str) -> dict[str, Any]:
        return {"tool_input": {"file_path": file_path}}

    return _make


@pytest.fixture
def bash_payload():
    def _make(command: str) -> dict[str, Any]:
        return {"tool_input": {"command": command}}

    return _make


@pytest.fixture
def edit_payload():
    def _make(file_path: str, new_string: str) -> dict[str, Any]:
        return {"tool_input": {"file_path": file_path, "new_string": new_string}}

    return _make


@pytest.fixture
def write_payload():
    def _make(file_path: str, content: str) -> dict[str, Any]:
        return {
            "tool_input": {"file_path": file_path},
            "tool_response": {"filePath": file_path, "content": content},
        }

    return _make
