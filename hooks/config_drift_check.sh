#!/usr/bin/env bash
# SessionStart hook: nudge when ~/.claude has drifted from the claude-dotfiles repo.
#
# The claude-dotfiles repo (sync.sh / install.sh) version-controls this config, but
# syncing is manual — so it's easy to edit a hook and forget to commit it. This
# hook closes that gap: at session start it runs `sync.sh --check` (read-only,
# never writes) and, if the live config has unsynced changes, prints a one-line
# reminder. SessionStart stdout reaches the session, so the drift is surfaced
# right when you sit down to work. Always exits 0 — informational, never blocks.

HOOK_DEBUG="${TMPDIR:-/tmp}/hook_debug.log"
_log() { printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "config_drift_check" "$*" >>"$HOOK_DEBUG"; }
_log "FIRED"

REPO="$HOME/projects/claude-dotfiles"
SYNC="$REPO/sync.sh"

# If the config repo isn't on this machine, there's nothing to nudge about.
if [[ ! -f "$SYNC" ]]; then
    _log "SKIP   claude-dotfiles repo not found"
    exit 0
fi

# `sync.sh --check` exits 0 when in sync (stay silent) and non-zero on drift.
if out="$(bash "$SYNC" --check 2>/dev/null)"; then
    _log "ALLOW  in sync"
    exit 0
fi

total="$(printf '%s\n' "$out" | sed -n 's/^DRIFT_TOTAL=//p')"
_log "ALLOW  drift=${total:-?}"
echo "⚠️  Your ~/.claude config has ${total:-some} unsynced change(s) vs the claude-dotfiles repo (hooks / settings / etc.)."
echo "   To version-control them, run:  dotfiles-sync \"<why you changed them>\""
echo "   (or 'bash $SYNC --check' to see exactly what drifted.)"
exit 0
