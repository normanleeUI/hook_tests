#!/usr/bin/env bash
# SessionStart hook: block if dependencies haven't been checked recently.
#
# Checks the mtime of .last_dep_check (or uv.lock as fallback).
# If older than DEP_CHECK_MAX_DAYS (default 30), exits 2 with instructions.
# To satisfy the check: run `uv lock --upgrade` then `touch .last_dep_check`,
# or just `touch .last_dep_check` to deliberately defer.

set -euo pipefail

printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "check_dep_freshness" "FIRED" >> "${TMPDIR:-/tmp}/hook_debug.log"

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# Only applies to projects with a uv.lock
if [[ ! -f "uv.lock" ]]; then
  exit 0
fi

DEP_CHECK_MAX_DAYS="${DEP_CHECK_MAX_DAYS:-30}"
MARKER=".last_dep_check"

# Use marker file if it exists, otherwise fall back to uv.lock mtime
if [[ -f "$MARKER" ]]; then
  reference="$MARKER"
else
  reference="uv.lock"
fi

# Get age in days
if [[ "$(uname)" == "Darwin" ]]; then
  last_modified=$(stat -f %m "$reference")
else
  last_modified=$(stat -c %Y "$reference")
fi
now=$(date +%s)
age_days=$(( (now - last_modified) / 86400 ))

if [[ $age_days -gt $DEP_CHECK_MAX_DAYS ]]; then
  cat <<EOF
{
  "systemMessage": "⚠️ Dependencies haven't been checked in ${age_days} days (threshold: ${DEP_CHECK_MAX_DAYS}).",
  "additionalContext": "Run 'uv lock --upgrade && touch .last_dep_check' to refresh, or 'touch .last_dep_check' to defer. Mention this to the user."
}
EOF
fi

echo "[check_dep_freshness] PASSED — last checked ${age_days} day(s) ago (threshold: ${DEP_CHECK_MAX_DAYS})" >&2
