"""Tests for hook_log._read_trigger defensive stdin handling.

log_hook() runs at the top of every Python hook (module import time) and calls
_read_trigger(), which reads the tool-call JSON off stdin to derive a cmd="..."
label. That read must never crash the hook — or, since it runs at import, the
module import. Two ways it used to break:

  - pytest output capture replaces sys.stdin with a sentinel whose .read()
    raises OSError → importing a hook for testing errored at collection
    (test_project_health_check.py). Regression guard: read failures degrade to
    an empty label instead of propagating.
  - a foreign stdin pipe (e.g. a caller's `while read` loop — the retired
    batch_checks.sh hit this) would be drained; fixed at the caller with
    </dev/null, but the label read here must still fail closed rather than
    raise.
"""

import io
import json
import os
import sys

# Honor HOOKS_DIR (set in CI to the vendored hooks/) so this import resolves
# without a live ~/.claude install; default to the real Claude hooks locally.
sys.path.insert(0, os.environ.get("HOOKS_DIR", os.path.expanduser("~/.claude/hooks")))
from hook_log import _read_trigger


class _RaisingStdin:
    """Mimics pytest's capture sentinel: not a tty, and .read() raises OSError."""

    def isatty(self) -> bool:
        return False

    def read(self, *args) -> str:
        raise OSError("reading from stdin while output is captured!")


class TestReadTriggerDefensive:
    def test_stdin_read_oserror_degrades_to_empty_label(self, monkeypatch):
        """The regression: a raising stdin (pytest capture) must not propagate."""
        monkeypatch.setattr(sys, "stdin", _RaisingStdin())
        assert _read_trigger() == ""

    def test_tty_stdin_returns_empty_without_reading(self, monkeypatch):
        """Interactive invocation (tty) short-circuits before any read."""

        class _Tty:
            def isatty(self) -> bool:
                return True

            def read(self, *args) -> str:
                raise AssertionError("must not read a tty")

        monkeypatch.setattr(sys, "stdin", _Tty())
        assert _read_trigger() == ""


class TestReadTriggerHappyPath:
    def test_command_payload_yields_label_and_restashes_stdin(self, monkeypatch):
        payload = json.dumps({"tool_input": {"command": "git status"}})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert _read_trigger() == 'cmd="git status"'
        # stdin is stashed back so the hook's own read still sees the payload
        assert sys.stdin.read() == payload

    def test_file_path_payload_yields_label(self, monkeypatch):
        payload = json.dumps({"tool_input": {"file_path": "/proj/app.py"}})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert _read_trigger() == 'cmd="/proj/app.py"'

    def test_non_json_stdin_degrades_to_empty_label(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json at all"))
        assert _read_trigger() == ""

    def test_long_trigger_is_truncated(self, monkeypatch):
        long_cmd = "x" * 200
        payload = json.dumps({"tool_input": {"command": long_cmd}})
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        label = _read_trigger()
        assert label == f'cmd="{"x" * 60}…"'
