#!/usr/bin/env bash
# SessionStart hook: pull latest from remote if CWD is a clean git repo.
# Outputs JSON with systemMessage (shown to user) and additionalContext
# (injected into model context). Exits 0 silently if not applicable.

printf '%s  %-35s  %s\n' "$(date +%H:%M:%S.%3N)" "git_pull_on_start" "FIRED" >> "${TMPDIR:-/tmp}/hook_debug.log"

# Not a git repo — nothing to do
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
  exit 0
fi

# No remote configured — nothing to pull from
remote=$(git remote 2>/dev/null | head -n1)
if [[ -z "$remote" ]]; then
  exit 0
fi

branch=$(git branch --show-current 2>/dev/null)
if [[ -z "$branch" ]]; then
  # Detached HEAD — skip
  exit 0
fi

# Check if this branch tracks a remote branch
tracking=$(git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null)
if [[ -z "$tracking" ]]; then
  exit 0
fi

# Verify network access before attempting pull. The sandbox may not provide
# direct connectivity after clearing the proxy, in which case git pull would
# fail with a DNS or connection error. Exit silently rather than producing a
# scary warning for something we can't fix.
if ! git ls-remote --exit-code --quiet "$remote" HEAD &>/dev/null; then
  exit 0
fi

# Check for uncommitted changes
if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
  cat <<EOF
{
  "systemMessage": "⚠️ Git remote found but working tree is dirty — skipped auto-pull.",
  "additionalContext": "The branch '${branch}' tracks '${tracking}' but has uncommitted changes, so auto-pull was skipped. Mention this to the user and offer to stash+pull or commit first."
}
EOF
  exit 0
fi

# Fetch and fast-forward only
if output=$(git pull --ff-only 2>&1); then
  if echo "$output" | grep -q "Already up to date"; then
    cat <<EOF
{
  "systemMessage": "✓ Git repo up to date with ${tracking}."
}
EOF
  else
    cat <<EOF
{
  "systemMessage": "✓ Pulled latest changes from ${tracking}.",
  "additionalContext": "Auto-pulled new commits from '${tracking}'. Output:\n${output}\nMention this to the user so they're aware of the new changes."
}
EOF
  fi
else
  cat <<EOF
{
  "systemMessage": "⚠️ git pull --ff-only failed — local and remote may have diverged.",
  "additionalContext": "Auto-pull from '${tracking}' failed. Output:\n${output}\nThis likely means local and remote have diverged. Mention this to the user and suggest resolving manually (rebase, merge, or force-pull)."
}
EOF
fi
