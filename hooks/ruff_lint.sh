#!/usr/bin/env bash
# Stop hook: lint-fix all staged/modified Python files.
#
# Runs `ruff check --fix` once across every dirty .py file right before
# Claude sends its final response. At this point all edits are complete,
# so rules like F401 (unused imports) fire correctly — they won't remove
# imports that are about to be used by a later edit.
#
# Uses `git diff --name-only` to find changed files (both staged and
# unstaged). Only .py files are passed to ruff.

HOOK_DEBUG="${TMPDIR:-/tmp}/hook_debug.log"
_log() { printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "ruff_lint" "$*" >> "$HOOK_DEBUG"; }
_log "FIRED  (pid=$$, TMPDIR=${TMPDIR:-unset}, cwd=$(pwd))"

toplevel=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$toplevel" ]; then
  _log "SKIP   not a git repo"
  exit 0
fi
cd "$toplevel" || exit 0
_log "CHDIR  $toplevel"

# Collect unique changed .py files (staged + unstaged)
files=$(git diff --name-only HEAD 2>/dev/null; git diff --name-only 2>/dev/null)
py_files=$(echo "$files" | tr -d '\r' | sort -u | grep '\.py$')
count=$(echo "$py_files" | grep -c . 2>/dev/null || echo 0)
_log "FILES  $count .py files changed"

if [ -n "$py_files" ]; then
  t0=$(date +%s%3N)
  echo "$py_files" | xargs .venv/bin/ruff check --fix >&2 2>&1
  rc=$?
  t1=$(date +%s%3N)
  elapsed=$(( t1 - t0 ))
  _log "DONE   ruff check --fix: ${elapsed}ms, exit=$rc"
else
  _log "DONE   no files to lint"
fi
