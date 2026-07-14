#!/usr/bin/env bash
# Stop hook: batch-run pyright, bandit, semgrep, docstring, and seed checks
# on all .py files changed during this turn.
#
# Runs each heavy tool (pyright, bandit, semgrep) ONCE on all files instead
# of per-file. This is 30-60x faster for large change sets.
# Always exits 0 — informational, never blocks.

HOOK_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_DEBUG="${TMPDIR:-/tmp}/hook_debug.log"
_log() { printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "batch_checks" "$*" >> "$HOOK_DEBUG"; }

_log "FIRED  (pid=$$, TMPDIR=${TMPDIR:-unset})"

toplevel=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$toplevel" ]; then
  _log "SKIP   not a git repo"
  exit 0
fi
cd "$toplevel" || exit 0

# Collect changed .py files (staged + unstaged), deduplicated
files=$(
  { git diff --name-only HEAD 2>/dev/null; git diff --name-only 2>/dev/null; } \
  | sort -u \
  | grep '\.py$'
)

if [ -z "$files" ]; then
  _log "DONE   no .py files changed"
  exit 0
fi

# Convert to absolute paths, skip deleted files and .claude/ paths
abs_files=""
while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in .claude/*|*/.claude/*) continue ;; esac
  abs_files="${abs_files}${toplevel}/${f}"$'\n'
done <<< "$files"
abs_files=$(echo "$abs_files" | sed '/^$/d')

file_count=$(echo "$abs_files" | wc -l)
_log "START  $file_count .py files to check (batch mode)"

# Filter out test files for bandit
non_test_files=$(echo "$abs_files" | grep -v -E '(^|/)test_|/tests/|_test\.py$')

batch_t0=$(date +%s%3N)

# --- Batch: pyright (skip if project uses mypy) ---
if ! grep -q '^\[tool\.mypy\]' pyproject.toml 2>/dev/null; then
  t0=$(date +%s%3N)
  echo "$abs_files" | python3 "$HOOK_DIR/inject_tool_findings.py" --batch PYRIGHT 2>/dev/null
  _log "  pyright: $(( $(date +%s%3N) - t0 ))ms"
fi

# --- Batch: bandit (non-test files only) ---
if [ -n "$non_test_files" ]; then
  t0=$(date +%s%3N)
  echo "$non_test_files" | python3 "$HOOK_DIR/inject_tool_findings.py" --batch BANDIT 2>/dev/null
  _log "  bandit: $(( $(date +%s%3N) - t0 ))ms"
fi

# --- Batch: semgrep ---
t0=$(date +%s%3N)
echo "$abs_files" | python3 "$HOOK_DIR/inject_tool_findings.py" --batch SEMGREP 2>/dev/null
_log "  semgrep: $(( $(date +%s%3N) - t0 ))ms"

# --- Per-file: docstrings and seeds (fast enough to keep per-file) ---
t0=$(date +%s%3N)
# NOTE: </dev/null on each script is load-bearing. Both scripts call
# log_hook(), which does sys.stdin.read() (to stash the tool-call JSON) when
# stdin isn't a tty. Without the redirect the first child drains this loop's
# piped file list, so the loop exits after ONE file and docstring/seed checks
# silently cover only the first changed file. Both scripts take the path as
# argv[1], so /dev/null is safe.
echo "$abs_files" | while IFS= read -r abs; do
  [ -n "$abs" ] || continue
  python3 "$HOOK_DIR/check_docstrings.py" "$abs" </dev/null 2>/dev/null
  python3 "$HOOK_DIR/check_random_seeds.py" "$abs" </dev/null 2>/dev/null
done
_log "  docstrings+seeds: $(( $(date +%s%3N) - t0 ))ms"

batch_t1=$(date +%s%3N)
total_ms=$(( batch_t1 - batch_t0 ))
_log "DONE   $file_count files checked in ${total_ms}ms ($(( total_ms / 1000 ))s)"

exit 0
