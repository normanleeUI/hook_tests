"""Unit tests for Phase 2 probe scripts.

These tests verify that channel_probe.py and hookspecific_probe.py produce
the expected stdout, stderr, and exit codes for known inputs. They run the
probe scripts via subprocess (not via the hook_runner helper, which is tied
to ~/.claude/hooks).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

PROBES_DIR = Path(__file__).parent

# Use a writable temp directory for probe input dumps (sandbox blocks /tmp writes).
_DUMP_DIR = os.environ.get("TMPDIR", tempfile.gettempdir())


def run_probe(script_name, payload, env=None):
    """Run a probe script with JSON payload on stdin, return (returncode, stderr, stdout)."""
    run_env = {**os.environ, "PROBE_DUMP_DIR": _DUMP_DIR, **(env or {})}
    result = subprocess.run(
        ["python3", str(PROBES_DIR / script_name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env=run_env,
    )
    return result.returncode, result.stderr, result.stdout


# --- channel_probe.py tests ---


def test_edit_probe_blocks():
    """channel_probe.py with Edit payload and PROBE_EXIT=2 exits 2 and emits PROBE marker on stderr."""
    payload = {
        "tool_input": {"file_path": "x.py", "old_string": "a", "new_string": "b"}
    }
    rc, stderr, _stdout = run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": "pre_edit_e2", "PROBE_EXIT": "2"},
    )
    assert rc == 2
    assert "PROBE" in stderr


def test_write_probe_blocks():
    """channel_probe.py with Write payload and PROBE_EXIT=2 exits 2 and emits PROBE marker on stderr."""
    payload = {"tool_input": {"file_path": "x.py", "content": "print(1)"}}
    rc, stderr, _stdout = run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": "pre_write_e2", "PROBE_EXIT": "2"},
    )
    assert rc == 2
    assert "PROBE" in stderr


def test_hookspecific_probe_outputs_json():
    """hookspecific_probe.py outputs valid JSON with hookSpecificOutput containing required fields."""
    payload = {"tool_input": {"file_path": "x.py"}, "hook_event_name": "PostToolUse"}
    rc, _stderr, stdout = run_probe("hookspecific_probe.py", payload)
    assert rc == 0
    parsed = json.loads(stdout)
    assert "hookSpecificOutput" in parsed
    assert "hookEventName" in parsed["hookSpecificOutput"]
    assert "additionalContext" in parsed["hookSpecificOutput"]


def test_webfetch_probe_blocks():
    """channel_probe.py with WebFetch payload and PROBE_EXIT=2 exits 2 and emits PROBE marker on stderr."""
    payload = {"tool_input": {"url": "https://example.com"}}
    rc, stderr, _stdout = run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": "pre_webfetch_e2", "PROBE_EXIT": "2"},
    )
    assert rc == 2
    assert "PROBE" in stderr


def test_bash_probe_blocks():
    """channel_probe.py with Bash payload and PROBE_EXIT=2 exits 2 and emits PROBE marker on stderr."""
    payload = {"tool_input": {"command": "echo probe_test"}}
    rc, stderr, _stdout = run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": "if_cond_e2", "PROBE_EXIT": "2"},
    )
    assert rc == 2
    assert "PROBE" in stderr


def test_channel_probe_stdout_contains_probe_id():
    """channel_probe.py stdout includes the PROBE_ID value."""
    payload = {"tool_input": {"command": "echo hi"}}
    _rc, _stderr, stdout = run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": "stdout_check", "PROBE_EXIT": "0"},
    )
    assert "stdout_check" in stdout


def test_channel_probe_stderr_contains_probe_id():
    """channel_probe.py stderr includes the PROBE_ID value."""
    payload = {"tool_input": {"command": "echo hi"}}
    _rc, stderr, _stdout = run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": "stderr_check", "PROBE_EXIT": "0"},
    )
    assert "stderr_check" in stderr


def test_channel_probe_exit_zero():
    """channel_probe.py exits 0 when PROBE_EXIT=0."""
    payload = {"tool_input": {"file_path": "x.py"}}
    rc, _stderr, _stdout = run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": "exit_zero", "PROBE_EXIT": "0"},
    )
    assert rc == 0


def test_channel_probe_writes_input_json_write_payload():
    """channel_probe.py writes Write-shaped input JSON to PROBE_DUMP_DIR/probe_input_<id>.json."""
    probe_id = "write_dump_test"
    dump_path = Path(_DUMP_DIR) / f"probe_input_{probe_id}.json"
    dump_path.unlink(missing_ok=True)

    payload = {"tool_input": {"file_path": "z.py", "content": "hello"}}
    run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": probe_id, "PROBE_EXIT": "0"},
    )

    assert dump_path.exists(), f"Expected {dump_path} to be written by channel_probe.py"
    written = json.loads(dump_path.read_text())
    assert written == payload
    dump_path.unlink(missing_ok=True)


def test_channel_probe_writes_input_json_edit_payload():
    """channel_probe.py writes Edit-shaped input JSON to PROBE_DUMP_DIR/probe_input_<id>.json."""
    probe_id = "edit_dump_test"
    dump_path = Path(_DUMP_DIR) / f"probe_input_{probe_id}.json"
    dump_path.unlink(missing_ok=True)

    payload = {
        "tool_input": {"file_path": "x.py", "old_string": "a", "new_string": "b"}
    }
    run_probe(
        "channel_probe.py",
        payload,
        env={"PROBE_ID": probe_id, "PROBE_EXIT": "0"},
    )

    assert dump_path.exists(), f"Expected {dump_path} to be written by channel_probe.py"
    written = json.loads(dump_path.read_text())
    assert written == payload
    dump_path.unlink(missing_ok=True)


def test_hookspecific_probe_additionalcontext_value():
    """hookspecific_probe.py additionalContext is the expected literal string."""
    payload = {"tool_input": {"file_path": "x.py"}, "hook_event_name": "PostToolUse"}
    _rc, _stderr, stdout = run_probe("hookspecific_probe.py", payload)
    parsed = json.loads(stdout)
    assert (
        parsed["hookSpecificOutput"]["additionalContext"]
        == "PROBE HSO: This text should reach the model"
    )
    assert parsed["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
