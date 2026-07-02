#!/usr/bin/env bash
# verify_prerequisites.sh — Pre-flight check for hook manual testing sessions.
#
# Verifies that every hook script, fixture file, tool dependency, and
# settings.json wiring is in place before starting a live testing session.
#
# Usage:  bash scripts/verify_prerequisites.sh
# Exit:   0 if all checks pass, 1 if any fail.

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
RESET='\033[0m'

PASS=0
FAIL=0

pass() {
    printf "  ${GREEN}✓${RESET} %s\n" "$1"
    PASS=$(( PASS + 1 ))
}

fail() {
    printf "  ${RED}✗${RESET} %s\n" "$1"
    FAIL=$(( FAIL + 1 ))
}

warn() {
    printf "  ${YELLOW}⚠${RESET} %s\n" "$1"
}

header() {
    printf "\n${BOLD}━━ %s ━━${RESET}\n" "$1"
}

# ── Resolve paths ────────────────────────────────────────────────────────────
HOOKS_DIR="$HOME/.claude/hooks"
SETTINGS="$HOME/.claude/settings.json"
# Project root is one level up from scripts/
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ═════════════════════════════════════════════════════════════════════════════
# 1. Hook scripts — existence + executable bit
# ═════════════════════════════════════════════════════════════════════════════
header "Hook scripts (exist + executable)"

PYTHON_HOOKS=(
    project_health_check.py
    block_read_env.py
    block_bare_pip.py
    scan_secrets_on_commit.py
    block_git_add_env.py
    pip_audit_guard.py
    check_dependency_pins.py
    block_suppressions.py
    block_glob_deny_rules.py
    check_docstrings.py
    check_random_seeds.py
    pip_audit_check.py
    scan_prompt_injection.py
)

SHELL_HOOKS=(
    git_pull_on_start.sh
    check_dep_freshness.sh
    ruff_format.sh
    pyright_check.sh
    bandit_check.sh
    semgrep_check.sh
    ruff_lint.sh
)

printf "\n  ${BOLD}Python hooks${RESET}\n"
for hook in "${PYTHON_HOOKS[@]}"; do
    path="$HOOKS_DIR/$hook"
    if [[ ! -f "$path" ]]; then
        fail "$hook  (missing)"
    elif [[ ! -x "$path" ]]; then
        fail "$hook  (exists but not executable)"
    else
        pass "$hook"
    fi
done

printf "\n  ${BOLD}Shell hooks${RESET}\n"
for hook in "${SHELL_HOOKS[@]}"; do
    path="$HOOKS_DIR/$hook"
    if [[ ! -f "$path" ]]; then
        fail "$hook  (missing)"
    elif [[ ! -x "$path" ]]; then
        fail "$hook  (exists but not executable)"
    else
        pass "$hook"
    fi
done

# ── Library files (not hooks, but hooks import them) ─────────────────────────
LIBRARY_FILES=(
    hook_inject.py
    hook_log.py
    inject_tool_findings.py
)

printf "\n  ${BOLD}Library files${RESET}\n"
for lib in "${LIBRARY_FILES[@]}"; do
    path="$HOOKS_DIR/$lib"
    if [[ ! -f "$path" ]]; then
        fail "$lib  (missing)"
    else
        pass "$lib"
    fi
done

# ═════════════════════════════════════════════════════════════════════════════
# 2. settings.json wiring
# ═════════════════════════════════════════════════════════════════════════════
header "settings.json wiring"

if [[ ! -f "$SETTINGS" ]]; then
    fail "settings.json not found at $SETTINGS"
else
    pass "settings.json exists"

    # Use python3 to parse and verify hook groups + total unique hook count.
    WIRING_RESULT=$(python3 - "$SETTINGS" <<'PYEOF'
import json, sys

settings_path = sys.argv[1]
with open(settings_path) as f:
    data = json.load(f)

hooks = data.get("hooks", {})

# Expected hook groups: (event, matcher_or_None)
# matcher=None means the group has no matcher (SessionStart, Stop).
expected_groups = [
    ("SessionStart", None),
    ("PostToolUse", "Edit|Write"),
    ("PostToolUse", "Bash"),
    ("PostToolUse", "WebFetch|mcp__.*"),
    ("Stop", None),
    ("PreToolUse", "Read"),
    ("PreToolUse", "Bash"),
    ("PreToolUse", "Edit|Write"),
]

found_groups = []
missing_groups = []
unique_commands = set()

for event, matcher in expected_groups:
    label = event if matcher is None else f"{event} [{matcher}]"
    event_list = hooks.get(event, [])
    matched = False
    for group in event_list:
        group_matcher = group.get("matcher")
        if matcher is None and group_matcher is None:
            matched = True
            for h in group.get("hooks", []):
                unique_commands.add(h.get("command", ""))
            break
        elif group_matcher == matcher:
            matched = True
            for h in group.get("hooks", []):
                unique_commands.add(h.get("command", ""))
            break
    if matched:
        found_groups.append(label)
    else:
        missing_groups.append(label)

# Output: one line per group (PASS/FAIL), then totals
for g in found_groups:
    print(f"PASS:{g}")
for g in missing_groups:
    print(f"FAIL:{g}")
print(f"TOTAL:{len(unique_commands)}")
PYEOF
    )

    while IFS= read -r line; do
        case "$line" in
            PASS:*)  pass "Hook group: ${line#PASS:}" ;;
            FAIL:*)  fail "Hook group: ${line#FAIL:}  (missing)" ;;
            TOTAL:*)
                total="${line#TOTAL:}"
                # 16 = 17 wired entries minus the block_read_env.py dup (wired on
                # both Read and Bash; counted once as a unique command string).
                # Down from 20 after the channel redesign folded pyright/bandit/
                # semgrep/docstrings/seeds into the single batch_checks.sh Stop hook.
                if [[ "$total" -eq 16 ]]; then
                    pass "Total hooks wired: $total (expected 16)"
                else
                    fail "Total hooks wired: $total (expected 16)"
                fi
                ;;
        esac
    done <<< "$WIRING_RESULT"
fi

# ═════════════════════════════════════════════════════════════════════════════
# 3. Fixture files
# ═════════════════════════════════════════════════════════════════════════════
header "Fixture files"

SRC_FIXTURES=(
    src/type_errors.py
    src/missing_docstrings.py
    src/security_issues.py
    src/unseeded_random.py
    src/has_suppressions.py
    src/clean_module.py
)

DIR_FIXTURES=(
    fixtures/staged_secret.py
    fixtures/glob_deny_settings.json
    fixtures/injection_payload.txt
    fixtures/unpinned_requirements.txt
)

ROOT_FIXTURES=(
    .env
    .env.example
)

printf "\n  ${BOLD}src/ fixtures${RESET}\n"
for f in "${SRC_FIXTURES[@]}"; do
    path="$PROJECT_DIR/$f"
    if [[ -f "$path" ]]; then
        pass "$f"
    else
        fail "$f  (missing)"
    fi
done

printf "\n  ${BOLD}fixtures/ directory${RESET}\n"
for f in "${DIR_FIXTURES[@]}"; do
    path="$PROJECT_DIR/$f"
    if [[ -f "$path" ]]; then
        pass "$f"
    else
        fail "$f  (missing)"
    fi
done

printf "\n  ${BOLD}Root files${RESET}\n"
for f in "${ROOT_FIXTURES[@]}"; do
    path="$PROJECT_DIR/$f"
    if [[ -f "$path" ]]; then
        pass "$f"
    else
        fail "$f  (missing)"
    fi
done

# ═════════════════════════════════════════════════════════════════════════════
# 4. Environment
# ═════════════════════════════════════════════════════════════════════════════
header "Environment"

# Python 3.11+
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 11 ]]; then
        pass "Python $PY_VERSION (>= 3.11)"
    else
        fail "Python $PY_VERSION (need >= 3.11)"
    fi
else
    fail "python3 not found"
fi

# uv
if command -v uv &>/dev/null; then
    UV_VER=$(uv --version 2>&1 | head -1)
    pass "uv available ($UV_VER)"
else
    fail "uv not found"
fi

# Virtual environment
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    pass "Virtual environment active ($VIRTUAL_ENV)"
elif [[ -d "$PROJECT_DIR/.venv" ]]; then
    # Check that the venv's python exists and works
    if "$PROJECT_DIR/.venv/bin/python" --version &>/dev/null; then
        pass "Virtual environment found at .venv (not currently activated)"
        warn "Run:  source $PROJECT_DIR/.venv/bin/activate"
    else
        fail "Virtual environment at .venv appears broken"
    fi
else
    fail "No virtual environment found (run: uv venv)"
fi

# Tool availability
TOOLS=(ruff pyright bandit semgrep)
for tool in "${TOOLS[@]}"; do
    if command -v "$tool" &>/dev/null; then
        pass "$tool available"
    elif [[ -x "$PROJECT_DIR/.venv/bin/$tool" ]]; then
        pass "$tool available (in .venv/bin/)"
    else
        fail "$tool not found on PATH or in .venv"
    fi
done

# Git repo with remote
if git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
    pass "Git repository"
    REMOTE=$(git -C "$PROJECT_DIR" remote -v 2>/dev/null | head -1 || true)
    if [[ -n "$REMOTE" ]]; then
        pass "Git remote configured"
    else
        fail "No git remote configured"
    fi
else
    fail "Not a git repository"
fi

# /tmp/hook_debug.log writable
# Hooks log to this file; verify we can write to it.
LOG_FILE="/tmp/hook_debug.log"
if touch "$LOG_FILE" 2>/dev/null && [[ -w "$LOG_FILE" ]]; then
    pass "$LOG_FILE is writable"
elif [[ -f "$LOG_FILE" && -w "$LOG_FILE" ]]; then
    pass "$LOG_FILE is writable"
else
    # Could be a sandbox restriction (e.g. Claude Code sandbox blocks /tmp).
    # Check if the file already exists and is readable as a weaker signal.
    if [[ -f "$LOG_FILE" ]]; then
        fail "$LOG_FILE exists but is not writable (sandbox or permissions issue)"
    else
        fail "$LOG_FILE does not exist and cannot be created"
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# 5. Final prep
# ═════════════════════════════════════════════════════════════════════════════
header "Final prep"

if (: > "$LOG_FILE") 2>/dev/null; then
    pass "Truncated $LOG_FILE (fresh start)"
else
    warn "Could not truncate $LOG_FILE (run outside sandbox to clear it)"
fi

# ═════════════════════════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════════════════════════
TOTAL=$(( PASS + FAIL ))
printf "\n${BOLD}━━ Summary ━━${RESET}\n"
if [[ "$FAIL" -eq 0 ]]; then
    printf "  ${GREEN}${BOLD}All checks passed: %d/%d${RESET}\n\n" "$PASS" "$TOTAL"
    exit 0
else
    printf "  ${RED}${BOLD}%d/%d checks passed (%d failed)${RESET}\n\n" "$PASS" "$TOTAL" "$FAIL"
    exit 1
fi
