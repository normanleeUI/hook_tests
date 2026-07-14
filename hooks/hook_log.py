"""Shared debug logger for hook observability.

log_hook() runs at the top of every Python hook. Per invocation it writes
two lines to the debug log:

    HH:MM:SS.mmm  <hook>  FIRED         cwd=<project>  cmd="<trigger>"
    HH:MM:SS.mmm  <hook>  BLOCK|ALLOW|ERROR  cwd=<project>  cmd="<trigger>"

- The FIRED line proves the hook ran (even if it later crashes).
- The second line records the *decision*, derived from the process exit code
  (2 = BLOCK, uncaught exception = ERROR, otherwise ALLOW) captured at
  teardown — so no individual hook needs to log its own outcome.
- cwd + cmd make a shared log cross-referenceable: filter by project when
  several Claude sessions run at once, and map a line to the command that
  triggered it.

The hook doesn't pass its command in: log_hook reads the tool-call JSON off
stdin itself and stashes it back (via a StringIO swap) so the hook's own
stdin read still works unchanged. This keeps every call site a plain
log_hook("<name>").
"""

import atexit
import datetime
import io
import json
import os
import pathlib
import sys

_LOG_FILE = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "hook_debug.log"


def _write(hook_name: str, outcome: str, cwd: str, detail: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"{ts}  {hook_name:<24s}  {outcome:<6s}  cwd={cwd}"
    if detail:
        line += f"  {detail}"
    with open(_LOG_FILE, "a") as f:
        f.write(line + "\n")


def _read_trigger() -> str:
    """Read the tool-call JSON off stdin, stash it back for the hook, and
    return a short cmd="..." label for the triggering command or file."""
    # The cmd label is observability sugar, not correctness. Reading stdin here
    # is fragile: under pytest's output capture sys.stdin.read() raises OSError,
    # and a closed/foreign fd can fail too. Guard it and degrade to no label
    # rather than crashing the hook — or, since log_hook() runs at module import,
    # crashing the import (which broke test_project_health_check collection).
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""  # interactive / no stdin — nothing to read
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return ""  # captured/closed stdin — skip the label, don't crash
    sys.stdin = io.StringIO(raw)  # ponytail: swap so the hook's own read still works
    try:
        tool_input = json.loads(raw).get("tool_input", {})
        trigger = str(tool_input.get("command") or tool_input.get("file_path") or "")
    except (ValueError, AttributeError, TypeError):
        return ""
    if not trigger:
        return ""
    trigger = trigger.replace("\n", " ")
    if len(trigger) > 60:
        trigger = trigger[:60] + "…"
    return f'cmd="{trigger}"'


def log_hook(hook_name: str, outcome: str = "FIRED") -> None:
    cwd = os.getcwd()
    detail = _read_trigger()
    _write(hook_name, outcome, cwd, detail)

    # Capture the decision at process teardown. sys.exit(2) means BLOCK; an
    # uncaught exception means ERROR; anything else is ALLOW. Wrapping
    # sys.exit + an excepthook lets us record this once, centrally, without
    # touching each hook's exit points.
    # ponytail: monkeypatch here is the minimal path; upgrade to explicit
    # log_decision() calls only if a hook needs a decision sys.exit can't express.
    state = {"outcome": "ALLOW"}
    real_exit = sys.exit

    def _exit(code=0):
        state["outcome"] = "BLOCK" if code == 2 else "ALLOW"
        real_exit(code)

    def _excepthook(exc_type, exc, tb):
        state["outcome"] = "ERROR"
        sys.__excepthook__(exc_type, exc, tb)

    sys.exit = _exit
    sys.excepthook = _excepthook
    atexit.register(lambda: _write(hook_name, state["outcome"], cwd, detail))
