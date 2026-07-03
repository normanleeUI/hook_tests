#!/usr/bin/env python3
"""Verify Batch 2 hook-wiring results from the debug log.

The Batch 2 agent prompts (see scripts/batch2_agent.md) run each command as a
separate Bash tool call. This script reads the resulting debug log and checks,
per command, that the right hook fired with the right decision — so
verification never depends on the agent's own narration, only on what the
hooks logged.

Usage:
    python3 scripts/check_batch2.py            # check all groups
    python3 scripts/check_batch2.py A B        # check only groups A, B

Reads ${TMPDIR:-/tmp}/hook_debug.log, filtered to the current project's cwd.
Exit code is nonzero if any expectation FAILs or is MISSING.
"""

import os
import pathlib
import re
import sys

LOG_PATH = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "hook_debug.log"
PROJECT = os.getcwd()

# (group, id, hook, exact command, expected outcome)
#   BLOCK / ALLOW = the hook fired with that decision (from its exit code)
#   FIRED         = the hook fired, decision unimportant (e.g. known-bug hooks)
#   NOT_FIRED     = the hook must NOT have run for this command (if: filtered)
EXPECTED = [
    # -- Group A: block_read_env read barrage --
    ("A", "2.1", "block_read_env", "cat .env", "BLOCK"),
    ("A", "2.2", "block_read_env", "head -5 .env.production", "BLOCK"),
    ("A", "2.3", "block_read_env", "base64 .env", "BLOCK"),
    ("A", "2.4", "block_read_env", "source .env", "BLOCK"),
    ("A", "2.5", "block_read_env", "python3 -c 'open(\".env\").read()'", "BLOCK"),
    ("A", "2.6", "block_read_env", "echo hello && cat .env", "BLOCK"),
    ("A", "2.7", "block_read_env", "cat .env.example", "ALLOW"),
    ("A", "2.8", "block_read_env", "cat .env.template", "ALLOW"),
    ("A", "2.9", "block_read_env", "cat README.md", "ALLOW"),
    # -- Group B: block_bare_pip + block_git_add_env --
    ("B", "2.10", "block_bare_pip", "pip install requests", "BLOCK"),
    ("B", "2.11", "block_bare_pip", "uv pip install requests", "ALLOW"),
    ("B", "2.11g", "pip_audit_guard", "uv pip install requests", "ALLOW"),
    ("B", "2.12", "block_bare_pip", "git status", "ALLOW"),
    ("B", "2.13", "block_git_add_env", "git add .env", "BLOCK"),
    ("B", "2.14", "block_git_add_env", "git add .", "BLOCK"),
    ("B", "2.15", "block_git_add_env", "git add src/clean_module.py", "ALLOW"),
    ("B", "2.16", "block_git_add_env", "git status", "NOT_FIRED"),
    # -- Group C: cross-cutting + if: filtering + scan_secrets --
    ("C", "2.17a", "block_bare_pip", "pip install requests", "BLOCK"),
    ("C", "2.17b", "block_read_env", "pip install requests", "ALLOW"),
    ("C", "2.17c", "pip_audit_guard", "pip install requests", "ALLOW"),
    ("C", "2.17d", "scan_secrets_on_commit", "pip install requests", "NOT_FIRED"),
    ("C", "2.18a", "block_git_add_env", "git add .env", "BLOCK"),
    ("C", "2.18b", "block_bare_pip", "git add .env", "ALLOW"),
    # With the realistic fixture staged (Batch 0), scan_secrets now BLOCKS the
    # commit; the file stays staged after each block, so all three commits block.
    ("C", "2.19a", "scan_secrets_on_commit", "git commit -m 'test'", "BLOCK"),
    ("C", "2.19b", "pip_audit_guard", "git commit -m 'test'", "ALLOW"),
    ("C", "2.20", "scan_secrets_on_commit", "git commit -m 'test wiring'", "BLOCK"),
    ("C", "2.21", "scan_secrets_on_commit", "git status", "NOT_FIRED"),
    ("C", "2.22", "block_git_add_env", "git add .", "BLOCK"),
    ("C", "2.23", "block_git_add_env", "echo hello", "NOT_FIRED"),
    ("C", "2.24", "scan_secrets_on_commit", "git commit -m 'secret test'", "BLOCK"),
]

# cmd= is always the last field, so grab everything after it and drop the
# closing quote — this survives commands that contain their own quotes.
LINE_RE = re.compile(r"^\S+\s+(\S+)\s+(\S+)\s+cwd=(\S+)(?:\s+cmd=\"(.*)\")?\s*$")

DECISIONS = {"BLOCK", "ALLOW", "ERROR"}


def parse_log():
    """Return decision records (hook, outcome, cmd) for this project only."""
    if not LOG_PATH.exists():
        sys.exit(f"No debug log at {LOG_PATH}")
    records = []
    for line in LOG_PATH.read_text().splitlines():
        m = LINE_RE.match(line)
        if not m:
            continue
        hook, outcome, cwd, cmd = m.groups()
        if cwd != PROJECT or outcome not in DECISIONS:
            continue
        records.append((hook, outcome, cmd or ""))
    return records


def evaluate(rows, records):
    results = []
    for group, tid, hook, cmd, expect in rows:
        matches = [o for h, o, c in records if h == hook and c == cmd]
        if expect == "NOT_FIRED":
            status = "PASS" if not matches else f"FAIL (fired: {matches})"
        elif expect == "FIRED":
            status = "PASS" if matches else "MISSING (never fired)"
        elif not matches:
            status = "MISSING (never fired / cmd altered?)"
        elif all(o == expect for o in matches):
            status = "PASS"
        else:
            status = f"FAIL (got {matches})"
        results.append((group, tid, hook, cmd, expect, status))
    return results


def main():
    wanted = {a.upper() for a in sys.argv[1:]} or {"A", "B", "C"}
    rows = [r for r in EXPECTED if r[0] in wanted]
    results = evaluate(rows, parse_log())

    width = max(len(c) for *_, c, _, _ in results) if results else 0
    failed = 0
    for group, tid, hook, cmd, expect, status in results:
        if not status.startswith("PASS"):
            failed += 1
        mark = "✓" if status.startswith("PASS") else "✗"
        print(f"  {mark} {tid:<6} {hook:<24} {cmd:<{width}}  {expect:<9} {status}")

    total = len(results)
    print(f"\n{total - failed}/{total} passed", end="")
    print(f" — {failed} need attention" if failed else " — all clear")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
