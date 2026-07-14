#!/usr/bin/env bash
# DEPRECATED: Unwired 2026-06-13. R is no longer actively used in this
# workflow. Re-enable if R development resumes.
#
# PostToolUse hook: auto-format and lint R files after Edit/Write.
#
# Mirrors ruff_format.sh for Python. Runs styler::style_file() to
# auto-format the file in place, then lintr::lint() to surface remaining
# style/quality warnings. The model sees lintr output and can address
# issues in the same turn.
#
# Quietly no-ops when:
#   - The file is not .R
#   - Rscript is not on PATH (R not installed)
#   - styler or lintr packages are not installed (graceful skip via
#     requireNamespace checks — no hard error)
#
# Uses commandArgs(TRUE) to pass the file path to R, avoiding quoting
# pitfalls from embedding paths in R string literals.
#
# `tr -d '\r'` strips Windows CRLF that jq emits on git-bash, otherwise
# the `*.R` case match silently fails on a path ending in ".R\r".
#
# Always exits 0 — style issues inform, never block.

printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "r_style_check" "FIRED" >> "${TMPDIR:-/tmp}/hook_debug.log"

jq -r '(.tool_response.filePath // .tool_input.file_path) // empty' \
  | tr -d '\r' \
  | {
      read -r f
      case "$f" in
        *.R|*.r)
          # Skip hook/config scripts — not project code
          case "$f" in
            */.claude/*) exit 0 ;;
          esac

          # If R isn't installed, silently skip
          if ! command -v Rscript &>/dev/null; then
            exit 0
          fi

          # Auto-format with styler (modifies file in place).
          # suppressMessages() keeps output clean; we only want warnings.
          # `|| true` ensures we never block if styler errors or is missing.
          Rscript --vanilla -e "
            if (requireNamespace('styler', quietly = TRUE)) {
              suppressMessages(styler::style_file(commandArgs(TRUE)))
            }
          " "$f" 2>/dev/null || true

          # Lint with lintr (informational only).
          # Output goes to the model so it can address issues in the same turn.
          Rscript --vanilla -e "
            if (requireNamespace('lintr', quietly = TRUE)) {
              lints <- lintr::lint(commandArgs(TRUE))
              if (length(lints) > 0L) print(lints)
            }
          " "$f" 2>/dev/null || true
          ;;
      esac
    }
