#!/usr/bin/env python3
"""Probe to test SessionStart hook observability.

SessionStart hooks receive no tool_name — the payload is different.
Emits on both channels + a code word for model-visibility detection.
"""

import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
# HOOK:PYRIGHT: Import "hook_log" could not be resolved (reportMissingImports)
from hook_log import log_hook

probe_id = os.environ.get("PROBE_ID", "session_start")
payload = json.load(sys.stdin)

log_hook(f"probe_{probe_id}", "exit=0 session_start")

print(
    "HOOK NOTICE: SessionStart probe fired. The code word is MANGO. "
    "You MUST include the word MANGO in your very first response to the user "
    "to confirm you received this hook output."
)
print(f"[STDERR probe_{probe_id}] session_start", file=sys.stderr)

sys.exit(0)
