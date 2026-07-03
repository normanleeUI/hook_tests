#!/usr/bin/env bash
# Install the tracked git pre-commit hook into this repo's .git/hooks/.
#
# Idempotent: safe to re-run; it just re-copies and re-marks executable.
#
# Why we copy into .git/hooks/ and do NOT set core.hooksPath:
#   Claude Code's worktree feature writes an ABSOLUTE
#   `core.hooksPath = <repo>/.git/hooks` into this repo's .git/config. So:
#     (a) we install the hook exactly where that pin already points — it fires;
#     (b) we must NOT set our own core.hooksPath (e.g. to .githooks): Claude Code
#         would overwrite it on its next worktree operation, silently disabling
#         the hook.
#   CAVEAT: because the pin is an ABSOLUTE path, if this repo is ever MOVED,
#   the pin goes stale (points at the old location). After a move, re-point
#   core.hooksPath and/or re-run this installer from the new location.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SRC="$REPO_ROOT/.githooks/pre-commit"
GIT_DIR="$(git rev-parse --git-dir)"
DEST="$GIT_DIR/hooks/pre-commit"

if [[ ! -f "$SRC" ]]; then
    echo "install_hooks.sh: source hook not found: $SRC" >&2
    exit 1
fi

mkdir -p "$GIT_DIR/hooks"
cp "$SRC" "$DEST"
chmod +x "$DEST"
echo "Installed pre-commit hook -> $DEST"
