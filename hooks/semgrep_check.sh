#!/usr/bin/env bash
# PostToolUse hook: security/quality-scan Python files after Edit/Write using semgrep.
#
# Delegates to inject_tool_findings.py to inject # HOOK:SEMGREP: comments
# directly into the file (inline injection pattern).
#
# Always exits 0 — semgrep findings inform, never block.
# Skips files in .claude/ (hooks/config scripts, not project code).
# Does NOT skip test files — unlike bandit, test code can contain genuinely
# security-relevant patterns (real network calls, unsafe deserialization).
# Silently no-ops on non-Python files.

printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "semgrep_check" "FIRED" >> "${TMPDIR:-/tmp}/hook_debug.log"

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

          python3 "$(dirname "$0")/inject_tool_findings.py" "$f" SEMGREP
          ;;
      esac
      exit 0
    }
