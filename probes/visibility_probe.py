#!/usr/bin/env python3
"""Visibility probe hook.

Filename convention: visibility_probe.py (single script, all copies are symlinks).
Behavior is controlled by the PROBE_ID env var set in each settings.json entry's
command string, e.g.:
    PROBE_ID=pre_read_e0 python3 /path/to/visibility_probe.py

Emits unique markers on both stdout and stderr so we can observe which channel
Claude Code surfaces for each event/matcher/exit-code combination.
"""

import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
from hook_log import log_hook

probe_id = os.environ.get("PROBE_ID", "unknown")
exit_code = int(probe_id.rsplit("_e", 1)[-1]) if "_e" in probe_id else 0

payload = json.load(sys.stdin)
tool = payload.get("tool_name", "unknown")

log_hook(f"probe_{probe_id}", f"exit={exit_code} tool={tool}")

print(f"[STDOUT probe_{probe_id}] tool={tool}")
print(f"[STDERR probe_{probe_id}] tool={tool}", file=sys.stderr)

sys.exit(exit_code)
