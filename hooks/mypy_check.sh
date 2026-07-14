#!/usr/bin/env bash
# DEPRECATED: Unwired 2026-06-13. Pyright is preferred (faster, stricter,
# higher spec conformance). Re-enable only if pyright proves insufficient.
#
# PreToolUse:Stop hook: run mypy on all dirty Python files (blocking).
#
# Runs once before the session ends, across every changed .py file.
# Exits 2 on type errors so the session must fix them before finishing.
# Skips tests and spikes (exploratory code).

printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "mypy_check" "FIRED" >> "${TMPDIR:-/tmp}/hook_debug.log"

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# Collect unique changed .py files (staged + unstaged)
files=$(git diff --name-only HEAD 2>/dev/null; git diff --name-only 2>/dev/null)
py_files=$(echo "$files" | tr -d '\r' | sort -u | grep '\.py$' | grep -v '^tests/' | grep -v '/spikes/' | grep -v '\.venv/' | while read -r f; do [ -f "$f" ] && echo "$f"; done)

if [ -z "$py_files" ]; then
  exit 0
fi

output=$(echo "$py_files" | xargs uvx --from "mypy>=1.10.0" mypy --ignore-missing-imports --no-error-summary 2>&1)
status=$?

if [ $status -ne 0 ]; then
  echo "$output" >&2
  echo "[mypy_check] Type errors found in changed files. Fix the type issues — do not use '# type: ignore'." >&2
  exit 2
fi
