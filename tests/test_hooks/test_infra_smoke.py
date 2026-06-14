"""Smoke tests for the shared test infrastructure (hook_runner)."""

from tests.test_hooks.hook_runner import run_hook


def test_run_hook_returns_tuple():
    """run_hook returns a 3-tuple with exit code 0 for a benign input."""
    result = run_hook(
        "block_read_env.py",
        {"tool_input": {"file_path": "/tmp/test.py"}},
    )
    assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
    assert len(result) == 3, f"Expected 3-tuple, got {len(result)}-tuple"
    code, stderr, stdout = result
    assert code == 0, f"Expected exit 0 for non-.env file, got {code}. stderr: {stderr}"
