#!/usr/bin/env bash
# PostToolUse hook: auto-format Python files after Edit/Write.
#
# Only runs `ruff format` (cosmetic whitespace/style). Lint-fix (`ruff check
# --fix`) is intentionally NOT here — it runs once at the end via
# ruff_lint.sh on the Stop hook. This avoids ruff removing "unused" imports
# mid-edit that are about to be referenced by code added in a later edit.
#
# `tr -d '\r'` strips Windows CRLF that jq emits on git-bash, otherwise the
# `*.py` case match silently fails on a path that ends in ".py\r".

HOOK_DEBUG="${TMPDIR:-/tmp}/hook_debug.log"
_log() { printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "ruff_format" "$*" >> "$HOOK_DEBUG"; }
_log "FIRED  (pid=$$)"

jq -r '(.tool_response.filePath // .tool_input.file_path) // empty' \
  | tr -d '\r' \
  | {
      read -r f
      _log "FILE   ${f:-<empty>}"
      case "$f" in
        *.py)
          toplevel=$(git -C "$(dirname "$f")" rev-parse --show-toplevel 2>/dev/null)
          ruff_bin="${toplevel:+$toplevel/.venv/bin/ruff}"
          if [ -x "$ruff_bin" ]; then
            flock "$f.hook_lock" "$ruff_bin" format "$f"
          else
            flock "$f.hook_lock" uvx ruff format "$f"
          fi
          rm -f "$f.hook_lock"
          _log "DONE   formatted $f"
          ;;
        *)
          _log "SKIP   not .py"
          ;;
      esac
    }
