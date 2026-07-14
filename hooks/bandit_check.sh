#!/usr/bin/env bash
# PostToolUse hook: security-scan Python files after Edit/Write using bandit.
#
# Delegates to inject_tool_findings.py to inject # HOOK:BANDIT: comments
# directly into the file (Strategy B: inline injection). This ensures the
# model sees security findings — stdout on PostToolUse exit-0 is a dead channel.
#
# Always exits 0 — security findings inform, never block, because bandit
# has a non-trivial false positive rate and blocking would be too disruptive.
# Skips files in .claude/ (hooks/config scripts, not project code).
# Skips test files — test fixtures often contain intentionally "insecure" patterns.
# Silently no-ops on non-Python files.

printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "bandit_check" "FIRED" >> "${TMPDIR:-/tmp}/hook_debug.log"

jq -r '(.tool_response.filePath // .tool_input.file_path) // empty' \
  | tr -d '\r' \
  | {
      read -r f
      case "$f" in
        *.py)
          # Skip hook/config scripts
          case "$f" in
            */.claude/*) exit 0 ;;
          esac

          # Skip test files — test fixtures often contain intentionally
          # "insecure" patterns (hardcoded strings, eval, etc.)
          case "$f" in
            */test_*|*/tests/*|*_test.py) exit 0 ;;
          esac

          python3 "$(dirname "$0")/inject_tool_findings.py" "$f" BANDIT
          ;;
      esac
      exit 0
    }
