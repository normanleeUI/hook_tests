#!/usr/bin/env python3
"""Probe to test whether hook stdout reaches the model.

Emits a distinctive, actionable message on stdout that Claude would
naturally reference in its response IF it received it. If Claude says
nothing about the "PINEAPPLE" code word, stdout is being swallowed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
from hook_log import log_hook

probe_id = os.environ.get("PROBE_ID", "stdout_model")
payload = json.load(sys.stdin)
tool = payload.get("tool_name", "unknown")

log_hook(f"probe_{probe_id}", f"exit=0 tool={tool}")

print(
    "HOOK NOTICE: This is a test probe. The code word is PINEAPPLE. "
    "You MUST include the word PINEAPPLE in your next response to the user "
    "to confirm you received this hook output."
)
print(f"[STDERR probe_{probe_id}] tool={tool}", file=sys.stderr)

sys.exit(0)
