# Interactive Hook Wiring Playbook

Manual QA checklist for verifying that Claude Code hooks **actually fire** in a live
session. Automated tests cover hook logic (correct decisions for given inputs). This
playbook tests the layer automated tests cannot reach: does Claude Code's runtime
invoke the hook, and does the output reach the user or model through a working channel?

Tests are batched by interaction pattern to minimize context-switching. Work through
the batches in order — some have dependencies on earlier results.

**Working style: log-and-continue.** When a test fails, record the finding and keep
going through the batch rather than stopping to fix each bug and re-run. Fixing
one-at-a-time forces a session restart per fix (the slow path) and can mask other
failures. Do a full pass first, then fix the surfaced bugs as a batch and add
regression tests. Note that hook fixes themselves live in `~/.claude/hooks/` (outside
this repo), so capture them in a commit message or changelog for provenance.

**Tools you'll use:**
- `./scripts/verify_prerequisites.sh` — pre-flight environment check
- `./scripts/observe.sh <hook> [file]` — post-test observation (debug log + inline comments + state files)
- `./scripts/observe.sh --all [file]` — show all hooks that fired (for cross-cutting tests)
- `./scripts/observe.sh --reset` — clear observation state between batches

---

## Reference: Observation Guide

Empirically validated output channels (see `probes/PROBE_RESULTS_PHASE2.md`):

| Code | Meaning | How to confirm | Status |
|------|---------|----------------|--------|
| **BLOCKED** | PreToolUse exit 2; action prevented | Claude reports block; stderr visible for Bash/Edit/Write | Working |
| **INLINE** | `# HOOK:<NAME>: <msg>` injected into edited file | Read file after edit — look for `# HOOK:` lines | Working (Strategy B) |
| **STATE-FILE** | Findings written to `.hook_state/<hook>/` | Check file existence/content | Working (Strategy C) |
| **SIDE-EFFECT** | Hook modifies files on disk | `git diff` after edit | Working |
| **MODEL-CONTEXT** | hookSpecificOutput.additionalContext → `<system-reminder>` | Claude's next response references it | Working (needs `hookEventName`) |
| **SESSION-STDOUT** | SessionStart stdout reaches model | Claude's first response reflects it | Working |
| ~~STATUS~~ | ~~statusMessage flash~~ | | **Never works** — ignore |
| ~~STDERR (exit 0)~~ | ~~stderr on allow~~ | | **Never visible** |

**Key limitations:**
- Read matcher exit-2 blocks show only a red dot (stderr not rendered), but the block works
- PostToolUse exit 2 is **cosmetic** — the edit already happened; only informs Claude
- PostToolUse hooks in the same group run **in parallel** — currently only ruff_format is in the Edit|Write group, so this is not a concern; batch_checks.sh runs tools sequentially
- ~~`if:` conditions work for single patterns~~; `|` OR syntax does NOT work.
  **⚠️ Regressed:** Claude Code 2.1.201 ignores the handler-level `if:` field
  entirely (see Known Issues, 2026-07-13). Scope is now enforced by in-body
  guards in the affected hooks, not the settings gate.

---

## Reference: Current Hook Wiring

17 active hooks across 7 groups:

| Event | Matcher | Hooks | Strategy |
|-------|---------|-------|----------|
| SessionStart | (none) | project_health_check, git_pull_on_start, check_dep_freshness | stdout to model |
| PreToolUse | Read | block_read_env | D (dual-wiring) |
| PreToolUse | Bash | block_read_env, block_no_verify, block_bare_pip, scan_secrets_on_commit (`if: git commit*`), block_git_add_env (`if: git add*`), pip_audit_guard | exit 2 blocks |
| PreToolUse | Edit\|Write | check_dependency_pins, block_suppressions, block_glob_deny_rules | A (promotion) |
| PostToolUse | Edit\|Write | ruff_format | SIDE-EFFECT (reformat) |
| PostToolUse | Bash | pip_audit_check | C (state-file) |
| PostToolUse | WebFetch\|mcp__.* | scan_prompt_injection | MODEL-CONTEXT |
| Stop | (none) | ruff_lint, batch_checks (pyright, bandit, semgrep, docstrings, seeds) | SIDE-EFFECT + inline injection |

---

## Git-native secret backstop (`.githooks/pre-commit`)

`scan_secrets_on_commit.py` is scoped `if: Bash(git commit*)`, which
`git -C . commit ...` bypasses — so a secret can reach `git commit` without
the Claude Code scan running. Option D closes this: a git-native `pre-commit`
hook (`.githooks/pre-commit`) fires at real commit time no matter how the
commit is invoked. It scans the staged diff for the same secret patterns and
also rejects any staged non-template `.env` file. It fails **closed** (blocks)
if `git diff` errors.

Install it (idempotent):

```bash
bash scripts/install_hooks.sh   # copies .githooks/pre-commit -> .git/hooks/pre-commit
```

- **`--no-verify` is blocked at the Claude Code layer**, not by git: git
  cannot stop a hook-skipping flag it is told to honor. `block_no_verify.py`
  (PreToolUse, Bash, no `if:`) exits 2 on any command containing `--no-verify`,
  including `git -C . commit --no-verify`.
- **CAVEAT — absolute `core.hooksPath` pin breaks on repo move**: Claude Code's
  worktree feature writes an *absolute* `core.hooksPath = <repo>/.git/hooks`
  into `.git/config`. The installer targets exactly that path and deliberately
  does **not** set its own `core.hooksPath` (Claude Code would overwrite it). If
  the repo is ever moved, the absolute pin goes stale — re-point it and re-run
  the installer from the new location.
- Tests: `tests/test_hooks/test_pre_commit_gate.py` (real `git commit` in a
  temp repo, including the `git -C` bypass) and
  `tests/test_hooks/test_block_no_verify.py`.

---

## Known Issues

- [x] **🟢 `if:` hook gating regressed to a no-op — FIXED via in-body guards (2026-07-13)**:
  **Claude Code 2.1.201 ignores the handler-level `if:` condition field entirely**
  (fails open), so `if:`-gated hooks fired on every command their `matcher`
  matched. Confirmed from the name-column debug log (`cwd=hook_tests`): both
  `block_git_add_env` (`if: Bash(git add*)`) and `scan_secrets_on_commit`
  (`if: Bash(git commit*)`) FIRED on plain `echo`/`cp`/`cd` commands with no
  `git` at all. The `if:` syntax in `settings.json` is actually correct per
  current docs — the build simply no longer honors it (P13 proved it worked in
  June, so genuine regression). `block_git_add_env`'s over-firing was *visible*:
  with no internal git-add check, it blocked any Bash command containing a bare
  non-template `.env` token. `scan_secrets`' was benign but wasteful (ran
  `git diff --cached` on every Bash command).
  **Fix (root-cause, harness-independent)**: pushed the scope guard *into* the
  hook bodies — `block_git_add_env.py` now early-exits unless the command is a
  real `git add` (matches `git add`, `git -C <p> add`, `git -c k=v add`);
  `scan_secrets_on_commit.py` now reads the command off stdin and early-exits
  unless it's a `git commit`. The `if:` fields are kept as cheap redundancy for
  when a future build honors them again. Verified: 14-case guard matrix + pytest
  regression suite (`tests/test_hooks/`). Hook edits live in `~/.claude/hooks/`
  (outside this repo) — provenance in the accompanying commit. Tests 2.16 / 2.21
  / 2.23 updated: their intent (hook inert on non-matching commands) holds, but
  the mechanism is now the in-body guard, so the observable is FIRED-but-ALLOW,
  not NOT-FIRED. **Follow-up**: the Observation Guide line "`if:` conditions work
  for single patterns" (still shown for historical P13 context) is now false for
  2.1.201 — left annotated rather than deleted.

- [x] **~~`scan_secrets_on_commit` logic bug~~ — RESOLVED (was a misdiagnosis)**:
  Previously believed `git diff --cached` returned empty in PreToolUse context.
  It does not. Verified end-to-end (2026-07-02): staging a realistic-length
  fake key and running `git commit` in a live session BLOCKS correctly, and an
  isolated run confirms `git diff --cached` sees staged content. The real cause
  of the earlier "fails open" observation was the Batch 0 fixture using
  `sk-ant-FAKE-key-here` — only 13 chars after the prefix, below the pattern's
  20-char floor, so it could never match. Fixed by staging the real fixture
  (`fixtures/staged_secret.py`) instead. The hook itself was always correct.

---

## Batch 0: Pre-session prep

Run in your normal terminal before starting any Claude session.

```bash
cd ~/projects/hook_tests

# 1. Verify environment
./scripts/verify_prerequisites.sh

# 2. Stage a file with a fake secret for scan_secrets_on_commit test (Batch 2).
# Copy the committed fixture (fixtures/staged_secret.py) rather than echoing a
# literal: its keys are real *length* (sk-ant-api03-… ≥20 chars) so the hook
# actually matches, and no matchable literal lands in this doc (which would
# make committing TESTING.md itself trip the hook).
cp fixtures/staged_secret.py src/staged_secret_test.py
git add src/staged_secret_test.py

# 3. Set stale dep marker for check_dep_freshness test (Batch 1)
touch -d "60 days ago" .last_dep_check

# 4. Ensure .env files exist for blocker tests
test -f .env.local      || echo 'DB_PASSWORD=fake-secret-for-testing' > .env.local
test -f .env.production  || echo 'SECRET_KEY=fake-prod-secret-for-testing' > .env.production

# 5. Clear stale state
rm -f .hook_state/pip_audit/report.json 2>/dev/null

# 6. Reset observation state
./scripts/observe.sh --reset
```

- [x] verify_prerequisites.sh passes
- [x] staged_secret_test.py is staged in git
- [x] .last_dep_check set to 60 days ago
- [x] .env, .env.local, .env.production exist
- [x] .hook_state/pip_audit/report.json cleared
- [x] observation state reset

---

## Batch 1: Session restarts (~15 min)

Each test requires a fresh Claude Code session. Do these first — they're the slowest.

### check_dep_freshness.sh

> **Wiring**: SessionStart, no matcher.
> **Observe via**: SESSION-STDOUT (if stale) + debug log.

**Test 1.1 — stale deps**
> Pre-condition: `.last_dep_check` set to 60 days ago (Batch 0).
> Start a new Claude Code session in `hook_tests/`.

- [x] Claude mentions stale dependencies in first response
- [x] `./scripts/observe.sh check_dep_freshness` — hook FIRED

**Test 1.2 — fresh deps**
> Run in normal terminal: `touch .last_dep_check`
> Start a new Claude Code session.

- [x] No staleness warning in Claude's first response
- [x] `./scripts/observe.sh check_dep_freshness` — hook FIRED (exited 0)

**Test 1.3 — missing marker file**
> Run in normal terminal: `rm -f .last_dep_check`
> Start a new Claude Code session.

- [x] No crash — hook handles gracefully
- [x] `./scripts/observe.sh check_dep_freshness` — hook FIRED

### project_health_check.py

> **Wiring**: SessionStart, no matcher.
> **Observe via**: SESSION-STDOUT.

**Test 1.4 — normal session (healthy project)**
> Start a new Claude Code session in `hook_tests/`. This repo passes all
> health checks, so the hook should stay SILENT (it only surfaces gaps —
> missing setup items, a CONTRIBUTING.md, or git stash/unpushed warnings).

- [x] No project-health output in Claude's first response (correct — project is healthy)
- [x] `./scripts/observe.sh project_health_check` — hook FIRED (exited 0)

**Test 1.5 — missing README**
> Run in normal terminal: `mv README.md README.md.bak`
> Start a new Claude Code session.

- [x] Health check flags missing README
- [x] `./scripts/observe.sh project_health_check` — hook FIRED
- [x] Run in normal terminal: `mv README.md.bak README.md` (restore)

### git_pull_on_start.sh

> **Wiring**: SessionStart, no matcher.
> **Observe via**: SIDE-EFFECT (pulls changes) + debug log.

**Test 1.6 — clean repo with remote**
> Start a new Claude Code session.

- [x] `./scripts/observe.sh git_pull_on_start` — hook FIRED

**Test 1.7 — uncommitted changes**
> Run in normal terminal: `echo "# temp" >> README.md`
> Start a new Claude Code session.

- [x] Hook skips pull (doesn't clobber uncommitted work)
- [x] `./scripts/observe.sh git_pull_on_start` — hook FIRED
- [x] Run in normal terminal: `git checkout -- README.md` (restore)

### Batch 1 reset

```bash
touch .last_dep_check  # restore normal state
./scripts/observe.sh --reset
```

---

## Batch 2: Bash command barrage (~10 min)

All "run this command, check if blocked" — rapid fire. Stay in one Claude session.
Phrase each as: "run `<command>`"

### block_read_env.py — Bash matcher (Strategy D dual-wiring)

> **Wiring**: PreToolUse, matcher=Bash, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible).
> Closes circumvention path where `cat .env` bypasses the Read tool block.

**Should BLOCK:**

- [x] 2.1: run `cat .env` — BLOCKED → `./scripts/observe.sh block_read_env`
- [x] 2.2: run `head -5 .env.production` — BLOCKED → `./scripts/observe.sh block_read_env`
- [x] 2.3: run `base64 .env` — BLOCKED → `./scripts/observe.sh block_read_env`
- [x] 2.4: run `source .env` — BLOCKED → `./scripts/observe.sh block_read_env`
- [x] 2.5: run `python3 -c 'open(".env").read()'` — BLOCKED → `./scripts/observe.sh block_read_env`
- [x] 2.6: run `echo hello && cat .env` — BLOCKED → `./scripts/observe.sh block_read_env`

**Should ALLOW:**

- [x] 2.7: run `cat .env.example` — allowed → `./scripts/observe.sh block_read_env`
- [x] 2.8: run `cat .env.template` — allowed → `./scripts/observe.sh block_read_env`
- [x] 2.9: run `cat README.md` — allowed → `./scripts/observe.sh block_read_env`

### block_bare_pip.py

> **Wiring**: PreToolUse, matcher=Bash, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible).
> Logic thoroughly tested by automated tests — manual test confirms wiring only.

- [x] 2.10: run `pip install requests` — BLOCKED → `./scripts/observe.sh block_bare_pip`
- [x] 2.11: run `uv pip install requests` — allowed → `./scripts/observe.sh block_bare_pip`
- [x] 2.12: run `git status` — allowed, hook fired → `./scripts/observe.sh block_bare_pip`

### block_git_add_env.py

> **Wiring**: PreToolUse, matcher=Bash. The settings `if: Bash(git add*)` gate is
> a **no-op** in Claude Code 2.1.201 (see Known Issues), so the git-add scope is
> now enforced by an **in-body guard** in `block_git_add_env.py`. The harness
> invokes the hook on every Bash command (it logs FIRED), but the guard makes it
> inert (ALLOW) on anything that isn't a real `git add`.
> **Observe via**: BLOCKED (exit 2, stderr visible) on a git-add of a `.env` file.

- [x] 2.13: run `git add .env` — BLOCKED → `./scripts/observe.sh block_git_add_env`
- [x] 2.14: run `git add .` — BLOCKED → `./scripts/observe.sh block_git_add_env`
- [x] 2.15: run `git add src/clean_module.py` — allowed → `./scripts/observe.sh block_git_add_env`
- [x] 2.16: run `git status` — FIRED but ALLOW (in-body guard skips non-git-add; `if:` no longer filters — see Known Issues) → `./scripts/observe.sh block_git_add_env`

### Cross-cutting: multiple Bash hooks share a matcher

> Multiple PreToolUse Bash hooks fire on every Bash command (unless `if:` filters).
> Focus on which OTHER hooks fired alongside the primary one.

- [x] 2.17: run `pip install requests` → `./scripts/observe.sh --all`
  - block_bare_pip BLOCKS
  - block_read_env (Bash) also fired? (yes — no `if:`)
  - scan_secrets_on_commit fired? (yes — harness invokes it since `if:` is a no-op;
    but its in-body guard skips: ALLOW, no `git diff`. Pre-regression this was "no")
- [x] 2.18: run `git add .env` → `./scripts/observe.sh --all`
  - block_git_add_env BLOCKS
  - block_bare_pip also fired? (yes — no `if:`)
- [x] 2.19: run `git commit -m 'test'` → `./scripts/observe.sh --all`
  - scan_secrets_on_commit FIRES
  - pip_audit_guard also fired? (yes — no `if:`)

### `if:` condition filtering — REGRESSED (see Known Issues)

> ⚠️ P13 once confirmed single-pattern `if:` worked, but Claude Code 2.1.201
> **ignores the handler-level `if:` field entirely** (verified 2026-07-13). Scope
> is now enforced by in-body guards in the two affected hooks. (`|` OR syntax
> still does NOT work either.) The gated hooks are INVOKED on every Bash command
> (they log FIRED) but their guards make them inert (ALLOW) outside their scope.

- [x] 2.20: run `git commit -m 'test wiring'` — scan_secrets FIRES and scans staged diff → `./scripts/observe.sh scan_secrets_on_commit`
- [x] 2.21: run `git status` — scan_secrets FIRED but ALLOW (in-body guard skips; no `git diff`) → `./scripts/observe.sh scan_secrets_on_commit`
- [x] 2.22: run `git add .` — block_git_add_env FIRES and BLOCKS (bulk add) → `./scripts/observe.sh block_git_add_env`
- [x] 2.23: run `echo hello` — block_git_add_env FIRED but ALLOW (in-body guard skips) → `./scripts/observe.sh block_git_add_env`

### scan_secrets_on_commit — resolved (fixture problem, not a hook bug)

> **Wiring**: PreToolUse, matcher=Bash. The `if: Bash(git commit*)` gate is a
> no-op in Claude Code 2.1.201 (see Known Issues); the git-commit scope is now
> enforced by an in-body guard in `scan_secrets_on_commit.py` (which also now
> reads the command off stdin, where before it scanned the staged diff on every
> Bash call).
> **Resolved (2026-07-02)**: the earlier "fails open" was the Batch 0 fixture
> using a too-short fake key, not a `git diff --cached` bug — see the Known
> Issues note above. With the real-length fixture staged, the hook BLOCKS
> correctly. The `if:` prefix-matcher bypass (`git -C . commit`) is closed
> separately by the git-native pre-commit backstop.

- [x] 2.24: run `git commit -m 'secret test'` — BLOCKED (real-length fixture staged) → `./scripts/observe.sh scan_secrets_on_commit`

```bash
# Clean up staged secret test file:
git reset HEAD src/staged_secret_test.py 2>/dev/null
rm -f src/staged_secret_test.py
./scripts/observe.sh --reset
```

### block_no_verify.py  *(added 2026-07-03 — new hook; short follow-up pass, run after the batch above)*

> **Wiring**: PreToolUse, matcher=Bash, no `if:`. Fires on **every** Bash command,
> so it also co-fires in the `--all` observations above (it's in the wiring table).
> **Observe via**: BLOCKED (exit 2, stderr visible).
> Blocks hook-skipping flags so `--no-verify` / `git commit -n` can't bypass the
> git-native pre-commit secret backstop. Safe to run live — it blocks, so no
> commit lands.

- [x] 2.25: run `git commit --no-verify -m x` — BLOCKED → `./scripts/observe.sh block_no_verify`
- [x] 2.26: run `git commit -n -m x` — BLOCKED (`-n` is the short form) → `./scripts/observe.sh block_no_verify`
- [x] 2.27: run `echo hello` — allowed, hook fired → `./scripts/observe.sh block_no_verify`

> **Verified 2026-07-13** (live session, name-column debug log, `cwd=hook_tests`):
> 2.25 `git commit --no-verify -m x` → FIRED/BLOCK; 2.26 `git commit -n -m x` →
> FIRED/BLOCK; 2.27 `echo hello` → FIRED/ALLOW. Aside: `block_no_verify` scans
> command *text*, so an `observe`/`grep` command that literally contains
> `git commit -n` or `--no-verify` is itself blocked — keep those tokens out of
> observation commands.

---

## Batch 3: Read tool tests (~3 min)

Phrase as: "read the file `<path>`"

### block_read_env.py — Read matcher

> **Wiring**: PreToolUse, matcher=Read, no `if:`.
> **Observe via**: BLOCKED (exit 2). Note: Read matcher blocks show only a red dot
> in the UI (stderr not rendered). The block itself works — Claude reports it as a tool error.

**Should BLOCK:**

- [x] 3.1: read the file `.env` — BLOCKED (red dot, tool error) → `./scripts/observe.sh block_read_env`
- [x] 3.2: read the file `.env.local` — BLOCKED → `./scripts/observe.sh block_read_env`
- [x] 3.3: read the file `.env.production` — BLOCKED → `./scripts/observe.sh block_read_env`

**Should ALLOW:**

- [x] 3.4: read the file `.env.example` — allowed (contents shown) → `./scripts/observe.sh block_read_env`
- [x] 3.5: read the file `src/clean_module.py` — allowed → `./scripts/observe.sh block_read_env`

> **Verified 2026-07-13** (live session, Read matcher, name-column debug log):
> `.env` / `.env.local` / `.env.production` → FIRED/BLOCK; `.env.example`
> (template allowlist) / `src/clean_module.py` → FIRED/ALLOW. The Bash `if:`
> regression (see Known Issues) does not affect Batch 3 — the Read matcher has no
> `if:` gate.
```bash
./scripts/observe.sh --reset
```

---

## Batch 4: pip_audit two-phase flow (~10 min)

**Order matters**: pip_audit_check (PostToolUse) must create the state file before
pip_audit_guard (PreToolUse) can read it. Network-dependent — may be slow.

### Step 1 — pip_audit_check creates state

> **Wiring**: PostToolUse, matcher=Bash, no `if:`.
> **Observe via**: STATE-FILE at `.hook_state/pip_audit/report.json`.
> Fires on every Bash command; internally filters for `uv add`/`uv sync`/`uv pip install`.

- [ ] 4.1: run `uv add httpx` — state file created → `./scripts/observe.sh pip_audit_check` then `cat .hook_state/pip_audit/report.json`
- [ ] 4.2: run `uv sync` — state file updated → `./scripts/observe.sh pip_audit_check`
- [ ] 4.3: run `git status` — hook fires but exits immediately (not a uv command) → `./scripts/observe.sh pip_audit_check`

### Step 2 — pip_audit_guard reads state and blocks

> **Wiring**: PreToolUse, matcher=Bash, no `if:`.
> **Observe via**: BLOCKED (exit 2) when state file has vulns; silent exit 0 otherwise.

- [ ] 4.4: run `uv add requests` — BLOCKED if vulns in state file, else allowed → `./scripts/observe.sh pip_audit_guard`
- [ ] 4.5: run `git status` — guard fires but exits 0 (not a uv command) → `./scripts/observe.sh pip_audit_guard`

### Step 3 — clean audit clears state

- [ ] 4.6: run `uv sync` — if no vulns, state file cleared → `ls -la .hook_state/pip_audit/report.json`
- [ ] 4.7: run `uv add requests` — allowed (no state file = no block) → `./scripts/observe.sh pip_audit_guard`

### Step 4 — negative: no state file at all

```bash
# Run in normal terminal:
rm -f .hook_state/pip_audit/report.json
```

- [ ] 4.8: run `uv add httpx` — pip_audit_guard exits 0 (no state file) → `./scripts/observe.sh pip_audit_guard`

```bash
./scripts/observe.sh --reset
```

---

## Batch 5: PreToolUse Edit|Write blockers (~5 min)

These hooks were promoted from PostToolUse to PreToolUse in the channel redesign.
exit 2 **genuinely prevents** the edit (unlike PostToolUse exit 2, which is cosmetic).

Phrase as: "edit `<file>` to `<change>`" or "add `<content>` to `<file>`"

### check_dependency_pins.py (Strategy A)

> **Wiring**: PreToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible) — edit prevented, file unchanged.

- [ ] 5.1: edit `pyproject.toml` to add `"requests"` as a dependency (no version pin) — BLOCKED, file unchanged → `./scripts/observe.sh check_dependency_pins` then `git diff pyproject.toml`
- [ ] 5.2: edit `pyproject.toml` to add `"requests==2.32.3"` as a dependency — allowed → `./scripts/observe.sh check_dependency_pins`
- [ ] 5.3: add a comment to `src/clean_module.py` — hook fires, exits 0 (not a dep file) → `./scripts/observe.sh check_dependency_pins`

### block_suppressions.py (Strategy A)

> **Wiring**: PreToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible) — edit prevented.

- [ ] 5.4: add `x = 1  # type: ignore` to `src/clean_module.py` — BLOCKED, line never written → `./scripts/observe.sh block_suppressions` then `git diff`
- [ ] 5.5: add `x = 1  # type: ignore[override]  # mypy-bug: reason` to `src/clean_module.py` — allowed → `./scripts/observe.sh block_suppressions`

### block_glob_deny_rules.py (Strategy A — file reconstruction)

> **Wiring**: PreToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: BLOCKED (exit 2, stderr visible) — edit prevented.
> Uses file reconstruction: reads current file, applies proposed edit in memory, checks result.

- [ ] 5.6: edit `.claude/settings.json` to add `Read(**/.env)` in a deny rule — BLOCKED → `./scripts/observe.sh block_glob_deny_rules`
- [ ] 5.7: edit `.claude/settings.json` to add `Read(.env)` (specific, not glob) — allowed → `./scripts/observe.sh block_glob_deny_rules`

> **Important**: Restore settings.json after test 5.7 if Claude modified it.

```bash
./scripts/observe.sh --reset
```

---

## Batch 6: Stop-hook batch checks + PostToolUse ruff_format (~15 min)

`batch_checks.sh` runs at **Stop** (end of turn) on all `.py` files changed
during the turn. It runs pyright, bandit, and semgrep once each in batch mode,
then check_docstrings and check_random_seeds per-file. All inject `# HOOK:<NAME>:`
comments into the source files.

`ruff_format.sh` remains a **PostToolUse Edit|Write** hook — it reformats
immediately after each edit.

**Testing pattern**: Ask Claude to make an edit, let the turn complete (Stop
fires), then check files and debug log from your normal terminal.

### ruff_format.sh (PostToolUse)

> **Wiring**: PostToolUse, matcher=Edit|Write, no `if:`.
> **Observe via**: SIDE-EFFECT (file reformatted on disk).

- [ ] 6.1: edit `src/clean_module.py` and add a function with bad formatting: `def    messy(  x,y  ) :  return   x+y` — file reformatted by ruff → `git diff src/clean_module.py`
- [ ] 6.2: edit `fixtures/sample_r_file.R` and add a comment — no formatting applied → `git diff fixtures/sample_r_file.R`

### batch_checks.sh (Stop)

> **Wiring**: Stop, no matcher.
> **Observe via**: INLINE — `# HOOK:PYRIGHT:`, `# HOOK:BANDIT:`, `# HOOK:SEMGREP:`,
> `# HOOK:DOCSTRING:`, `# HOOK:SEED:` comments injected into changed `.py` files.
> `batch_checks.sh` finds changed files via `git diff`, skips deleted files and
> `.claude/` paths, skips non-`.py` files.

**Test 6.3 — trigger file with known issues**

> Ask Claude to edit `src/type_errors.py` — add a blank line at the end. Let the
> turn complete. Then check from your normal terminal:

- [ ] `./scripts/observe.sh batch_checks src/type_errors.py` — hook FIRED, `# HOOK:PYRIGHT:` comments at type error lines
- [ ] Claude's next turn sees the inline comments (they're in the file)

**Test 6.4 — clean file**

> Ask Claude to edit `src/clean_module.py` — add a blank line. Let the turn complete.

- [ ] `./scripts/observe.sh batch_checks src/clean_module.py` — hook FIRED, no `# HOOK:` comments (clean file)

**Test 6.5 — non-Python file skipped**

> Ask Claude to edit `fixtures/sample_r_file.R` — add a comment. Let the turn complete.

- [ ] `./scripts/observe.sh batch_checks` — hook FIRED, but `.R` file has no `# HOOK:` comments

**Test 6.6 — docstrings**

> Ask Claude to edit `src/missing_docstrings.py` — add a blank line. Let the turn complete.

- [ ] `./scripts/observe.sh batch_checks src/missing_docstrings.py` — `# HOOK:DOCSTRING:` at undocumented functions

**Test 6.7 — test files excluded from docstring checks**

> Ask Claude to edit any `test_*.py` file — add a blank line. Let the turn complete.

- [ ] No `# HOOK:DOCSTRING:` comments (test files excluded)

**Test 6.8 — random seeds**

> Ask Claude to edit `src/unseeded_random.py` — add a blank line. Let the turn complete.

- [ ] `./scripts/observe.sh batch_checks src/unseeded_random.py` — `# HOOK:SEED:` at unseeded usage

**Test 6.9 — security issues**

> Ask Claude to edit `src/security_issues.py` — add a blank line. Let the turn complete.

- [ ] `./scripts/observe.sh batch_checks src/security_issues.py` — `# HOOK:BANDIT:` and/or `# HOOK:SEMGREP:` at security issues

### Integration: multi-trigger file

> Ask Claude to edit `src/multi_trigger.py` — add a blank line. Let the turn complete.

- [ ] 6.10: `./scripts/observe.sh --all src/multi_trigger.py` — MULTIPLE `# HOOK:` comments: PYRIGHT, BANDIT, DOCSTRING, SEED, possibly SEMGREP
- [ ] 6.11: debug log shows batch_checks ran each tool sequentially → `grep batch_checks /tmp/hook_debug.log | tail -10`
- [ ] 6.12: ask Claude to edit `src/multi_trigger.py` again, let turn complete — stale `# HOOK:` comments cleaned up, fresh ones injected

```bash
./scripts/observe.sh --reset
```

---

## Batch 7: Cross-cutting — PreToolUse blocks vs. Stop batch checks (~5 min)

When a PreToolUse Edit|Write hook blocks (exit 2), the edit never happens, so
ruff_format (PostToolUse) has nothing to process. The Stop-hook batch_checks
still runs, but finds files via `git diff` — a blocked edit means no diff, so
it has nothing to check for that file either.

**Test 7.1 — blocked edit produces no downstream effects**

> Ask Claude to edit `src/clean_module.py` and add a line `x = 1  # type: ignore`.
> Let the turn complete.

- [ ] block_suppressions (PreToolUse) BLOCKS the edit
- [ ] `git diff` shows `src/clean_module.py` unchanged
- [ ] `./scripts/observe.sh --all src/clean_module.py` — ruff_format did NOT run on this file, no `# HOOK:` comments

**Test 7.2 — blocked edit doesn't prevent batch_checks on other files**

> In the same turn, ask Claude to also edit `src/type_errors.py` (add a blank line).
> The suppression edit is blocked, but the type_errors edit should succeed.

- [ ] `src/clean_module.py` unchanged (blocked)
- [ ] `src/type_errors.py` has `# HOOK:PYRIGHT:` comments (batch_checks ran on this file)
- [ ] `./scripts/observe.sh batch_checks` — hook FIRED

```bash
./scripts/observe.sh --reset
```

---

## Batch 8: Miscellaneous (~10 min)

### scan_prompt_injection.py

> **Wiring**: PostToolUse, matcher=WebFetch|mcp__.*, no `if:`.
> **Observe via**: MODEL-CONTEXT — output reaches Claude as `<system-reminder>`.
> Hook must include `hookEventName` field (undocumented requirement discovered during probes).

> **Setup** (if test 8.1 needs a local server for injection content):
> ```bash
> # In another terminal:
> cd ~/projects/hook_tests/fixtures && python3 -m http.server 8888 &
> ```
> Then ask Claude to fetch `http://localhost:8888/injection_payload.txt`

- [ ] 8.1: fetch a URL with injection content (e.g., `http://localhost:8888/injection_payload.txt`) — Claude acknowledges injection risk → `./scripts/observe.sh scan_prompt_injection`
- [ ] 8.2: fetch a clean URL (e.g., `https://httpbin.org/get`) — no injection warning → `./scripts/observe.sh scan_prompt_injection`
- [ ] 8.3: edit a `.py` file — hook does NOT fire (wrong matcher) → `./scripts/observe.sh scan_prompt_injection`

### ruff_lint.sh — Stop hook

> **Wiring**: Stop, no matcher.
> **Observe via**: SIDE-EFFECT (files auto-fixed after turn ends).
> Note: batch_checks.sh also fires at Stop. These tests focus on ruff_lint behavior;
> batch_checks was covered in Batch 6.

- [ ] 8.4: edit `src/clean_module.py` and add `import os` at the top (unused import), let the turn complete — after Claude finishes, unused import auto-removed → `git diff src/clean_module.py`
- [ ] 8.5: edit `src/clean_module.py` (already clean) — add a docstring tweak — no side effects → `git diff src/clean_module.py`
- [ ] 8.6: ask Claude a question (no file edits) — nothing happens → `./scripts/observe.sh ruff_lint`
- [ ] 8.7: edit `fixtures/sample_r_file.R` — add a comment — ruff_lint does not touch non-.py files → `git diff fixtures/sample_r_file.R`

---

## Cleanup

Run in your normal terminal after all testing:

```bash
# Restore any modified files
git checkout -- src/ fixtures/ pyproject.toml 2>/dev/null

# Remove test artifacts
git reset HEAD src/staged_secret_test.py 2>/dev/null
rm -f src/staged_secret_test.py
rm -f src/*.hook_lock
rm -f .env.local .env.production  # if created during prep

# Restore normal state
touch .last_dep_check

echo "Testing complete — review checkboxes above for results."
```

---

## Fixture Files Reference

**Source files** (`src/`):
- `type_errors.py` — deliberate type errors for pyright
- `missing_docstrings.py` — functions without docstrings
- `security_issues.py` — eval(), subprocess.call(shell=True)
- `unseeded_random.py` — random/numpy usage without seeds
- `has_suppressions.py` — type: ignore, noqa comments
- `clean_module.py` — clean file that should pass all hooks
- `multi_trigger.py` — triggers pyright, bandit, semgrep, docstrings, random seeds simultaneously

**Fixture files** (`fixtures/`):
- `staged_secret.py` — contains fake API key for secret scanning
- `glob_deny_settings.json` — settings with dangerous glob patterns
- `injection_payload.txt` — prompt injection text samples
- `unpinned_requirements.txt` — dependencies without version pins

**State directories** (gitignored):
- `.hook_state/pip_audit/` — pip_audit_check findings

---

## Follow-up: Secret Detection False Positives

**Correction (2026-07-02):** an earlier version of this note claimed the hook
fires on short dummy strings like `sk-ant-FAKE-...`. It does not — the Anthropic
pattern requires 20+ chars after the prefix, and `sk-ant-FAKE-key-here` has only
13. That claim predated the `{20,}` tightening and is no longer accurate.

The real residual false positive is **committed fixture files that contain
intentionally-fake but pattern-matching keys.** `fixtures/staged_secret.py`
holds `sk-ant-api03-…` (≥20 chars, matches by design), so editing and then
committing *that file* is blocked by the hook — it can't tell a deliberate test
fixture from a real leak. (The playbook itself dodges this: Batch 0 copies the
fixture into an untracked `src/staged_secret_test.py` that cleanup removes and
never commits.)

**If this becomes a friction point**, mitigations in rough order of preference:
- **Inline allowlist marker** — skip lines carrying a sentinel comment such as
  `# pragma: allowlist secret` (the detect-secrets convention). Precise, opt-in
  per line, no path-based blind spots.
- **Path allowlist** — skip `fixtures/` and `tests/`. Simplest, but a real
  secret committed under those paths would pass; tolerable only because test
  dirs should never hold live secrets.
- Length/entropy thresholds do **not** help here — the fixture keys are
  realistic length on purpose.
