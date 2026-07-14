#!/usr/bin/env bash
# PostToolUse hook: type-check Python files after Edit/Write using pyright.
#
# Delegates to inject_tool_findings.py to inject # HOOK:PYRIGHT: comments
# directly into the file (Strategy B: inline injection). This ensures the
# model sees type errors — stdout on PostToolUse exit-0 is a dead channel.
#
# Always exits 0 — type errors inform, never block.
# Skips files in .claude/ (hooks/config scripts, not project code).
# Skips projects that use mypy (avoid duplicate type checking).
# Silently no-ops on non-Python files.
#
# `tr -d '\r'` strips Windows CRLF that jq emits on git-bash.

printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "pyright_check" "FIRED" >> "${TMPDIR:-/tmp}/hook_debug.log"

jq -r '(.tool_response.filePath // .tool_input.file_path) // empty' \
  | tr -d '\r' \
  | {
      read -r f
      case "$f" in
        *.py)
          # Skip hook/config scripts — pyright noise on these isn't useful
          case "$f" in
            */.claude/*) exit 0 ;;
          esac

          # Skip if the project already uses mypy (avoid duplicate type checking).
          # Walk up from the file to find the nearest pyproject.toml and check
          # for a [tool.mypy] section, which means the project chose mypy.
          dir="$(dirname "$f")"
          while [ "$dir" != "/" ]; do
            if [ -f "$dir/pyproject.toml" ]; then
              if grep -q '^\[tool\.mypy\]' "$dir/pyproject.toml" 2>/dev/null; then
                exit 0
              fi
              break
            fi
            dir="$(dirname "$dir")"
          done

          python3 "$(dirname "$0")/inject_tool_findings.py" "$f" PYRIGHT
          ;;
      esac
      exit 0
    }
