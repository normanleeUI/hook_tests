#!/usr/bin/env bash
set -euo pipefail

# observe.sh — quick post-test observer for manual hook testing
#
# Usage:
#   ./scripts/observe.sh <hook_name> [target_file]   Show what happened for a specific hook
#   ./scripts/observe.sh --all [target_file]          Show all hooks that fired recently
#   ./scripts/observe.sh --reset                      Clear state for a fresh test

# ---------------------------------------------------------------------------
# Colors (disabled if stdout is not a terminal)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' CYAN='' BOLD='' DIM='' RESET=''
fi

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEBUG_LOG='/tmp/hook_debug.log'
MARKER_FILE="${TMPDIR:-/tmp}/last_observe_ts"
HOOK_STATE_DIR='.hook_state'
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
header() {
    echo -e "\n${BOLD}${CYAN}── $1 ──${RESET}"
}

ok()   { echo -e "  ${GREEN}$1${RESET}"; }
warn() { echo -e "  ${YELLOW}$1${RESET}"; }
err()  { echo -e "  ${RED}$1${RESET}"; }
dim()  { echo -e "  ${DIM}$1${RESET}"; }

usage() {
    echo "Usage:"
    echo "  $(basename "$0") <hook_name> [target_file]"
    echo "  $(basename "$0") --all [target_file]"
    echo "  $(basename "$0") --reset"
    exit 1
}

# ---------------------------------------------------------------------------
# --reset mode
# ---------------------------------------------------------------------------
if [[ "${1:-}" == '--reset' ]]; then
    truncate -s 0 "$DEBUG_LOG" 2>/dev/null || true
    date '+%H:%M:%S.000' > "$MARKER_FILE"
    ok "Observation state reset — ready for next test"
    exit 0
fi

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
if [[ $# -lt 1 ]]; then
    usage
fi

SHOW_ALL=false
HOOK_NAME=''
TARGET_FILE=''

if [[ "$1" == '--all' ]]; then
    SHOW_ALL=true
    TARGET_FILE="${2:-}"
else
    HOOK_NAME="$1"
    TARGET_FILE="${2:-}"
fi

# ---------------------------------------------------------------------------
# 1. Debug log
# ---------------------------------------------------------------------------
header "Debug log (${DEBUG_LOG})"

if [[ ! -f "$DEBUG_LOG" ]]; then
    err "No debug log found"
else
    # Determine the "since" timestamp from the marker file
    since='00:00:00.000'
    if [[ -f "$MARKER_FILE" ]]; then
        since="$(cat "$MARKER_FILE")"
    fi

    # Extract matching lines — either all hooks or a specific one
    # Log format: "HH:MM:SS.mmm  hook_name   FIRED"
    matches=()
    while IFS= read -r line; do
        ts="${line%% *}"
        # Scope to this project: the shared /tmp log is written by every
        # concurrent Claude session, so drop lines whose cwd= tag points at a
        # different project. Lines without a cwd= tag (older shell hooks) are
        # kept, since they can't be attributed either way.
        if [[ "$line" == *"cwd="* && "$line" != *"cwd=$PROJECT_ROOT"* ]]; then
            continue
        fi
        # String comparison works for HH:MM:SS.mmm since it's lexicographic
        if [[ "$ts" > "$since" || "$ts" == "$since" ]]; then
            if $SHOW_ALL; then
                matches+=("$line")
            else
                # Check if this line contains the hook name
                if [[ "$line" == *"$HOOK_NAME"* ]]; then
                    matches+=("$line")
                fi
            fi
        fi
    done < "$DEBUG_LOG"

    if [[ ${#matches[@]} -gt 0 ]]; then
        for m in "${matches[@]}"; do
            dim "$m"
        done
        if $SHOW_ALL; then
            ok "${#matches[@]} hook firing(s) since $since"
        else
            ok "hook FIRED (${#matches[@]}x since $since)"
        fi
    else
        if $SHOW_ALL; then
            err "No hooks fired since $since"
        else
            err "hook DID NOT FIRE ($HOOK_NAME not found since $since)"
        fi
    fi

    # Update the marker so the next observation starts fresh
    date '+%H:%M:%S.000' > "$MARKER_FILE"
fi

# ---------------------------------------------------------------------------
# 2. Inline injection (only if target_file provided)
# ---------------------------------------------------------------------------
if [[ -n "$TARGET_FILE" ]]; then
    header "Inline comments (${TARGET_FILE})"

    if [[ ! -f "$TARGET_FILE" ]]; then
        err "File not found: $TARGET_FILE"
    else
        count=0
        while IFS= read -r line; do
            lineno="${line%%:*}"
            content="${line#*:}"
            dim "line ${lineno}: ${content}"
            count=$((count + 1))
        done < <(grep -n '# HOOK:' "$TARGET_FILE" 2>/dev/null || true)

        if [[ $count -gt 0 ]]; then
            ok "$count inline comment(s) found"
        else
            warn "0 inline comments found"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 3. State files
# ---------------------------------------------------------------------------
header "State files (${HOOK_STATE_DIR}/)"

state_dir="${PROJECT_ROOT}/${HOOK_STATE_DIR}"
if [[ ! -d "$state_dir" ]]; then
    warn "No .hook_state/ directory"
else
    # Show files modified in the last 5 minutes
    recent_files=()
    while IFS= read -r f; do
        recent_files+=("$f")
    done < <(find "$state_dir" -type f -mmin -5 2>/dev/null || true)

    if [[ ${#recent_files[@]} -gt 0 ]]; then
        for f in "${recent_files[@]}"; do
            rel="${f#"$state_dir"/}"
            dim "$rel (modified <5m ago)"
        done
        ok "${#recent_files[@]} recently modified state file(s)"
    else
        dim "No state files modified in the last 5 minutes"
    fi
fi

# ---------------------------------------------------------------------------
# 4. Lock files
# ---------------------------------------------------------------------------
header "Lock files"

if [[ -n "$TARGET_FILE" ]]; then
    search_dir="$(dirname "$TARGET_FILE")"
else
    search_dir="$PROJECT_ROOT"
fi

lock_files=()
while IFS= read -r f; do
    lock_files+=("$f")
done < <(find "$search_dir" -maxdepth 2 -name '*.hook_lock' 2>/dev/null || true)

if [[ ${#lock_files[@]} -gt 0 ]]; then
    for f in "${lock_files[@]}"; do
        warn "$f"
    done
    warn "${#lock_files[@]} lock file(s) present"
else
    ok "No lock files"
fi

echo ""
