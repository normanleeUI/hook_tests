#!/usr/bin/env python3
import json
import sys

data = json.load(sys.stdin)
output = {
    "hookSpecificOutput": {
        "hookEventName": data.get("hook_event_name", "PostToolUse"),
        "additionalContext": "PROBE HSO: This text should reach the model",
    }
}
json.dump(output, sys.stdout)
sys.exit(0)
