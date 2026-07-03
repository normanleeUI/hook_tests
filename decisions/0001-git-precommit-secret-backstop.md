# ADR 0001 — Git-native pre-commit hook as the secret-commit backstop

**Status:** Accepted (2026-07-03)
**Deciders:** Norman (with Claude Code)

## Context

Secrets must never enter git history. The existing guard, the global Claude
Code PreToolUse hook `~/.claude/hooks/scan_secrets_on_commit.py`, is wired with
`"if": "Bash(git commit*)"`. That `if:` is a **prefix matcher against the shell
command string**, and it is bypassable:

- Confirmed live: `git -C . commit -m x` does **not** match `git commit*`, so the
  scan is skipped and a staged secret commits cleanly. (`git -C`, `bash -c "git
  commit …"`, `eval`, subshells, and aliases are the same class.)
- The matcher *does* decompose `&&`/`;` compounds (so `x && git commit` is
  caught) — the gap is invocation *forms*, not chaining.

The sibling hook `block_git_add_env.py` (`if: Bash(git add*)`) has the identical
weakness for staging `.env` files. Pattern-matching arbitrary shell text is
inherently leaky, but `scan_secrets_on_commit` is billed as "the LAST line of
defense" — a bypassable last line is a false guarantee.

A relevant environment fact shaped the fix: **Claude Code's worktree feature
writes an absolute `core.hooksPath = <repo>/.git/hooks` into each repo's
`.git/config`** (verified in the binary and in `.git/worktrees/*/config.worktree`).
Consequences: (a) a machine-global `core.hooksPath` is *ignored* by every such
repo (local wins) — so global is both harmless and useless here; (b) the pin is
absolute, so moving a repo makes it stale — this is exactly why
`example-lib`'s pre-commit hook silently stopped firing after it moved into
`solo/`.

## Decision

Adopt **option D: a git-native `pre-commit` hook as the real guarantee, with the
Claude Code hooks retained as the early/UX layer.**

1. **`.githooks/pre-commit`** (tracked, self-contained, no `~/.claude` imports)
   scans the staged diff for the same secret patterns as
   `scan_secrets_on_commit.py` (copied verbatim, with a sync note) and rejects
   staged non-template `.env` files. It fires at real commit time regardless of
   how the commit is invoked, closing the `git -C` class of bypass. Fails
   **closed** (aborts) if `git diff` errors.
2. **Install into `<repo>/.git/hooks/pre-commit`** via `scripts/install_hooks.sh`,
   riding Claude Code's existing absolute `core.hooksPath` pin. We deliberately do
   **not** set our own `core.hooksPath` — Claude Code would overwrite it on its
   next worktree operation and silently disable the hook.
3. **Block hook-skipping flags at the Claude Code layer.** A git hook cannot stop
   `--no-verify`/`-n` (git is told to skip it), so a new global hook
   `~/.claude/hooks/block_no_verify.py` (PreToolUse, Bash, no `if:`) exits 2 on
   `--no-verify` or a `git commit` carrying a `-n` short flag (standalone or
   bundled, e.g. `-nm`), scoped to the commit's own args to avoid false-positives
   on a chained `-n`.

Repo-local was chosen over machine-global because global is neutered by the
existing local pins and would break on repo moves; per-repo install into
`.git/hooks` is predictable and rides the tool instead of fighting it.

## Consequences

**Positive**
- Closes every commit *invocation-form* bypass (`git -C`, `bash -c`, `eval`,
  subshell, alias) — git fires the hook from its own machinery.
- Protects commits made outside Claude Code entirely (plain terminal, IDE).
- `--no-verify`, `-n`, and `-nm` are all caught before git runs.
- Reproducible: hook source is versioned; install is one idempotent script.

**Negative / residual (accepted)**
- `git -c core.hooksPath=/dev/null commit …` disables hooks for that one command
  without `--no-verify`/`-n`, so neither layer catches it. Fully closing every
  skip vector is whack-a-mole; **truly un-skippable enforcement needs a
  server-side `pre-receive` hook** on the remote — out of scope for a local repo.
- The secret `PATTERNS` dict is duplicated between the global hook and
  `.githooks/pre-commit` (they live in different trust domains). Mitigated by a
  source comment; must be re-synced if the global patterns change.
- The absolute `core.hooksPath` pin means **moving the repo breaks the hook** —
  re-run `scripts/install_hooks.sh` (and re-point the pin) after any move.
- Install is a manual per-clone step (the sandbox cannot write `.git/hooks`).
- `block_no_verify` may false-positive on a commit whose *message* contains a
  `-n` token — acceptable; it fails safe.

**Deferred**
- Server-side `pre-receive` hook for un-skippable enforcement, if this repo ever
  gains a controlled remote.
- Fixing `example-lib`'s stale `core.hooksPath` (tracked in that project).

## Alternatives considered

- **A — more `if:` patterns** (`git -C* commit*`, …): rejected; can't enumerate
  `bash -c`/`eval`/aliases, and `if:` has no OR support. Whack-a-mole.
- **B — drop `if:`, detect commit-intent inside the CC hook**: closes agent-typed
  bypasses but not commits made outside Claude Code, and stays heuristic. Lighter
  but weaker than D.
- **Machine-global `core.hooksPath`**: neutered by existing local pins and fragile
  on repo moves (see Context). Rejected.

## References

- Hooks: `.githooks/pre-commit`, `scripts/install_hooks.sh`,
  `~/.claude/hooks/block_no_verify.py`, `~/.claude/hooks/scan_secrets_on_commit.py`
- Tests: `tests/test_hooks/test_pre_commit_gate.py` (incl. the `git -C` case),
  `tests/test_hooks/test_block_no_verify.py` (incl. `-n`/`-nm`)
- Playbook: `TESTING.md` → "Git-native secret backstop"
