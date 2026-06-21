#!/usr/bin/env python3
import json
import os
import sys
import traceback

probe_id = os.environ.get("PROBE_ID", "unknown")
probe_exit = int(os.environ.get("PROBE_EXIT", "0"))
dump_dir = os.environ.get("PROBE_DUMP_DIR", "/tmp")

try:
    data = json.load(sys.stdin)
    with open(f"{dump_dir}/probe_input_{probe_id}.json", "w") as f:
        json.dump(data, f, indent=2)
except Exception:
    with open(f"{dump_dir}/probe_crash_{probe_id}.log", "w") as f:
        traceback.print_exc(file=f)
    raise

print(f"PROBE {probe_id}: stdout test", file=sys.stdout)
print(f"PROBE {probe_id}: stderr test", file=sys.stderr)
sys.exit(probe_exit)
