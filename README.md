# Claude Code Hooks: Findings & Test Harness

A worked investigation into **Claude Code hooks** — which of their output
channels actually reach you and the model, which silently do nothing, and how
to test hooks so you find out *before* you rely on one. It ships:

- **A reference table of empirically-verified hook channel behavior** (below) —
  the reusable takeaway, even if you never run the code.
- **A reference table of ~20 real hooks** (security guards, linters, injection
  scanners) — see [the table below](#the-hooks). The vendored copies were
  removed 2026-08-07 after drifting from the live implementations under test
  (the harness runs hooks from `~/.claude/hooks` via `HOOKS_DIR`, so the local
  copies were a stale edit-the-wrong-file trap); they remain in git history.
  [`hooks/`](hooks/) now holds only the unshipped `block_unresolved_findings.py`
  commit gate and its two helpers.
- **A pytest/Hypothesis harness** (`tests/test_hooks/`) that verifies both hook
  *logic* (does the script decide correctly?) and hook *wiring* (does Claude
  Code actually fire it?).

## Why this exists

Claude Code hooks are configured in `settings.json` and fire on lifecycle
events (a tool is about to run, a session starts, the model stops). The docs
tell you the *shape* of a hook, but not which output channels are actually
delivered in practice — and several documented-looking channels **do nothing at
all.**

This project started from a real failure: a `pip_audit` hook that was *silently
never firing*. Its `if:` condition used `|` as an OR separator (which looks
reasonable, matches the `matcher` syntax, and is wrong — see findings). Nothing
errored; it just never ran. That class of bug — a hook that looks wired but is
inert — is exactly what this repo exists to catch.

## Major findings

**The single most useful artifact here.** Determined by systematic probing
(see [`probes/`](probes/)) and live wiring runs (see [`TESTING.md`](TESTING.md)).
Every "works / doesn't" below is observed behavior, not documentation.

### Which output channels actually work

| Channel | Works? | Notes |
|---|---|---|
| `statusMessage` (the flash line) | ❌ **Never** | No event / matcher / exit-code combination surfaces it. |
| `stderr` on **exit 0** | ❌ Never | Invisible on every matcher. If you only exit 0, stderr is lost. |
| `stderr` on **exit 2** + `Bash` matcher | ✅ Yes | Renders to you as an error *and* the model receives it. Real block. |
| `stderr` on **exit 2** + `Edit`/`Write` matcher | ⚠️ Cosmetic | Shown, but on **PostToolUse the edit already happened** — the block does not revert it. |
| `stderr` on **exit 2** + `Read` matcher | ⚠️ Weak | You see only a bare red dot (no message); the model gets the stderr and may route around it with `cat`. |
| `stdout` on Pre/PostToolUse | ❌ Never | 12 tests, 0 reached the model. |
| `stdout` on **SessionStart** | ✅ Yes | Reliably reaches the model (3/3). This is how to inject startup context. |
| `hookSpecificOutput.additionalContext` | ✅ Yes | Delivered to the model as a `<system-reminder>`. **Requires an undocumented `hookEventName` field** or Claude Code rejects the output. |
| Side effects (the hook edits a file) | ✅ Yes | Hooks act on the real file on disk. The most robust channel. |

**Practical consequence:** only three channels reliably move information —
**PreToolUse+Bash exit-2** (to block with a visible reason), **SessionStart
stdout** (to brief the model), and **direct file side-effects**. Design hooks
around those; treat everything else as decorative.

### Hooks in the same group fire in *parallel*, not sequentially

Every hook wired to the same event+matcher group is launched **concurrently** —
there is no ordering guarantee and no serialization between them. This is easy
to get wrong: we first concluded "sequential" from a fast/slow hook pair, which
turned out to be a lesson in probe design, not hook behavior (see the addendum
in [`probes/PROBE_RESULTS_PHASE2.md`](probes/PROBE_RESULTS_PHASE2.md)).

Parallelism is harmless until two hooks touch the same mutable state. Hooks
that **inject `# HOOK:<TOOL>:` comments** (the now-retired
`inject_tool_findings.py` and docstring/seed checks) each did a *read →
modify → write* of the same source file. Run concurrently, they race: the last
writer wins and the other hook's comments silently vanish. A read-modify-write
across a shared file is the canonical shape of this bug.

**This is what the `.hook_lock` file is for.** Each injector takes an exclusive
`fcntl.flock` on a **per-target-file** lock at `<filepath>.hook_lock`, holds it
across the whole read-analyze-write, then releases and unlinks it — see
[`hooks/hook_inject.py`](hooks/hook_inject.py) `read_clean_write()`. Because the
lock is per file (not one global lock), injections into *different* files still
run in parallel. Two caveats worth stating honestly:

- Only the **injection** hooks lock. Hooks that merely *block* (exit 2) or write
  their own [state file](#the-hooks) share no mutable file with a sibling, so
  they don't need one.
- The injector fleet is retired (2026-08: pyright/bandit/semgrep/docstring
  checks moved to the git pre-commit hook in claude-config `githooks/`, which
  reports on stderr instead of rewriting files; the seed leg was dropped).
  The lock helper survives in `hook_inject.py` for any future injector: the
  moment two injectors are wired into one group again, the race is back.

### Other behavioral gotchas that cost real debugging time

- **PostToolUse exit-2 is cosmetic.** The tool already ran; exit 2 only informs
  the model. To actually prevent an action, you must be on **PreToolUse**.
- **The `if:` field does not support `|` OR syntax** (unlike `matcher`).
  `"if": "Bash(*uv add*)|Bash(*uv sync*)"` matches nothing and silently disables
  the hook. Use one condition, or guard inside the script.
- **`hookEventName` is required inside `hookSpecificOutput`** but undocumented —
  omit it and the whole output is discarded.

### Delivering a comment is not the same as getting it heeded

The inline `# HOOK:<TOOL>:` channel is the most *reliable delivery* path (a real
file on disk) and the *weakest compliance* one. Delivery and compliance are
independent axes, and only the first is a channel property — the second is
discretionary. A bare diagnostic comment (`# HOOK:BANDIT: [B105] hardcoded
password`) reads as pre-existing churn: in a controlled probe
([`probes/HEED_PROBE.md`](probes/HEED_PROBE.md)), agents given an unrelated edit
task **ignored it 3/3**, editing right past a real finding.

Reframing the *same* finding on the *same* channel — an addressed imperative
with provenance (`[automated guardrail]`) and a truthful consequence
(`re-inserted until resolved`) — moved acknowledgement to **6/6** in a fresh
run, with a 3/3-ignored bare control alongside (rules out session drift). The
change is one shared construction point,
[`hooks/hook_inject.py`](hooks/hook_inject.py) `inject_at_line()`, so every
injector inherits it. Ceiling worth stating: a passive comment tops out at
*acknowledgement* — agents still (correctly) decline to fix an out-of-scope
finding. Forcing the fix needs an active gate (a PreToolUse block), not better
words.

### pip-audit was auditing the wrong environment — and missed `uv run` entirely

Two independent defects that together meant the dependency audit was effectively
inert, both found by driving the hook end-to-end against a known-vulnerable pin
(`jinja2==2.11.2`):

- **Bare `uvx pip-audit` audits uvx's *isolated* tool env, not your project.** A
  vulnerable pin came back `All dependencies clean (0 packages audited)` — every
  "clean" it ever reported was auditing nothing. Fix: export the locked deps and
  audit *that* (`uv export … | uvx pip-audit -r <file>`), which correctly flags
  all 5 `jinja2` advisories. The old unit tests passed against this broken hook
  because they mocked `uvx` away and never checked *what* it audited — the
  regression test now asserts pip-audit is pointed at the exported deps.
- **The trigger missed `uv run`.** The hook only fired on `uv add`/`uv
  sync`/`uv pip install`, but `uv run` implicitly re-syncs the env from the
  lockfile — so a project whose whole session used only `uv run` (the common
  case) was never audited. Fix: also trigger on `uv run`, gated on `uv.lock`'s
  content actually changing (sha256) so the constant `uv run` calls stay a cheap
  no-op. See [`hooks/pip_audit_check.py`](hooks/pip_audit_check.py).

Compounding both, a clean audit reports via **stderr on exit 0** — an invisible
channel (see the table above) — so even a *working* audit says nothing you can
see; the state file is the real signal.

### Version-sensitive — read this before trusting the above

Claude Code's hook behavior **changes between versions.** These findings were
verified on the versions noted in [`TESTING.md`](TESTING.md) (most recently
**2.1.201**, mid-2026). One already regressed:

> **`if:` handler-level gating regressed to a no-op in 2.1.201** — the field is
> now ignored (fails *open*), so an `if:`-scoped hook fires on *every* command
> its `matcher` matches. It provably worked in June 2026. Scope is now enforced
> by in-body guards inside the hooks, not the settings gate.

Treat the tables as "true as of the tested version" and re-probe on your own
version. The harness is how you re-verify: run it against your Claude Code and
see what still holds.

## What's in the repo

```
hooks/                 # only the unshipped commit-gate trio; vendored copies removed 2026-08-07 (see git history)
tests/test_hooks/      # pytest + Hypothesis: logic tests + wiring validation
probes/                # standalone experiments that discovered the channel behavior above
fixtures/              # test inputs (fake secrets, unpinned deps, injection payloads)
src/                   # deliberately-bad "trigger" modules that trip specific hooks
plans/                 # the implementation plans behind the harness
decisions/             # ADRs (e.g. the git-native secret backstop)
.githooks/pre-commit   # git-native secret backstop (see below)
TESTING.md             # the live wiring playbook + version-stamped observations
```

> **Note on the fixtures and `hooks/`:** the "secrets" in `fixtures/` and in the
> secret-scanning hooks are **synthetic** — AWS's documented `EXAMPLE` key,
> `sk_live_fake…`, PEM *headers* with no key body. They exist to exercise the
> scanners. There are no real credentials anywhere in this repo or its history.

## The hooks

Every hook in the tested setup — what it does, when it fires, and **which
channel it uses to convey information** — because, per the findings above, the
channel is the whole game. The channels used here:

- **Block (exit 2)** — refuses the action with a visible reason (only real on `PreToolUse`).
- **Startup context** — JSON on SessionStart stdout: `systemMessage` (shown to you) + `additionalContext` (injected into the model).
- **Model context** — `hookSpecificOutput.additionalContext`, delivered to the model as a `<system-reminder>`.
- **File rewrite** — edits the target file directly (formatting / lint-fix).
- **Inline comment** — writes `# HOOK:<TOOL>:` comments into the source at the relevant lines so the model sees them on the next read.
- **State file** — writes to `.hook_state/` for a companion hook to read later.

These document one developer's real setup; treat them as worked examples,
not a library.

### SessionStart — once, when a session begins

| Hook | Channel | What it does |
|---|---|---|
| `project_health_check.py` | Startup context | Injects a project-health reminder (git state, stale artifacts) into the model's startup context. |
| `git_pull_on_start.sh` | Startup context | Pulls latest from remote when the tree is clean; warns instead when it's dirty. |
| `check_dep_freshness.sh` | Startup context | Warns when dependencies haven't been checked recently. |
| `config_drift_check.sh` | Startup context (plain stdout) | Nudges when `~/.claude` has drifted from the `claude-dotfiles` source repo. |

### PreToolUse — before a tool runs; **can block** (exit 2)

| Hook | Fires on | Channel | What it does |
|---|---|---|---|
| `block_read_env.py` | `Read`, `Bash` | Block (exit 2) | Blocks reading `.env` files (points you at `.env.example`). |
| `block_bare_pip.py` | `Bash` | Block (exit 2) | Blocks bare `pip install` outside a uv/venv environment. |
| `block_no_verify.py` | `Bash` | Block (exit 2) | Blocks `git commit --no-verify` / `-n` (which would skip the pre-commit gate). |
| `scan_secrets_on_commit.py` | `Bash` (`git commit*`) | Block (exit 2) | Scans the staged diff for secret patterns and blocks the commit if any match. |
| `block_git_add_env.py` | `Bash` (`git add*`) | Block (exit 2) | Blocks staging a non-template `.env` file. |
| `pip_audit_guard.py` | `Bash` | Block (exit 2) | Blocks dependency operations while an unresolved pip-audit vulnerability is on record. |
| `block_glob_deny_rules.py` | `Edit`, `Write` | Block (exit 2) | Blocks writing overly-broad `**` Read deny rules into `settings.json`. |
| `check_dependency_pins.py` | `Edit`, `Write` | Block (exit 2) | Blocks adding unpinned dependency versions. |
| `block_suppressions.py` | `Edit`, `Write` | Block (exit 2) | Blocks unjustified `# type: ignore` / `# noqa` comments. |

### PostToolUse — after a tool runs; informational (can't undo the action)

| Hook | Fires on | Channel | What it does |
|---|---|---|---|
| `ruff_format.sh` | `Edit`, `Write` | File rewrite | Auto-formats the edited Python file with `ruff format`. |
| `pip_audit_check.py` | `Bash` | State file | Runs pip-audit after `uv add`/`uv sync` and records findings for `pip_audit_guard.py`. |
| `scan_prompt_injection.py` | `WebFetch`, `mcp__*` | Model context | Scans fetched/MCP tool output for prompt-injection patterns and warns the model. |

### Stop — once, when the model finishes a turn

| Hook | Channel | What it does |
|---|---|---|
| `ruff_lint.sh` | File rewrite | Runs `ruff check --fix` once across every changed `.py` file. |

### Retired 2026-08 — superseded by the git pre-commit hook

The `batch_checks.sh` Stop hook and its injectors (`inject_tool_findings.py`,
`check_docstrings.py`, `check_random_seeds.py`) are unwired and their tests
removed. Pyright, bandit, semgrep, and docstring checks now run in the
claude-config `githooks/pre-commit` hook (covered by
`tests/test_hooks/test_precommit_hook.py` and
`tests/test_hooks/test_docstring_analysis.py`); the seed leg was dropped
system-wide.

### Shared libraries — imported by the hooks, not fired directly

| Module | What it does |
|---|---|
| `hook_log.py` | Shared debug logger + central outcome recorder (`FIRED`/`BLOCK`/`ALLOW`). |
| `hook_inject.py` | Shared inline-injection and `.hook_state/` state-file helpers. |

## Running the tests

```bash
uv venv
uv sync --extra dev
```

The harness invokes each hook via `subprocess`, piping the JSON payload Claude
Code would send, and checks exit codes (0 = allow, 2 = block), stderr, and file
side-effects. Point it at the hooks you want to test with `HOOKS_DIR`:

```bash
# Against the vendored reference hooks in this repo (self-contained):
HOOKS_DIR="$PWD/hooks" pytest

# Against your own live hooks (validates YOUR setup, incl. settings.json wiring):
pytest                       # defaults to ~/.claude/hooks + ~/.claude/settings.json
```

The wiring tests validate a live `~/.claude/settings.json` against a canonical
registry (`tests/test_hooks/test_hook_wiring.py`); they **skip** when there is
no live config (CI, fresh clone) — there's nothing to validate without one.
Network- and Hypothesis-marked tests are deselected by default; run them
explicitly with `-m network` / `-m hypothesis`.

## Git-native secret backstop

Claude Code's `scan_secrets_on_commit` hook is scoped `if: Bash(git commit*)`,
which `git -C . commit` (and subshells, aliases, `eval`) bypass.
[`.githooks/pre-commit`](.githooks/pre-commit) is a self-contained, git-native
backstop that fires at real commit time regardless of how the commit is invoked
— it scans the staged diff for the same secret patterns and rejects staged
non-template `.env` files, failing closed if `git diff` errors. Install it with:

```bash
bash scripts/install_hooks.sh
```

`git commit --no-verify` would skip this git hook, so it is blocked one layer
earlier by the `block_no_verify.py` hook. See
[`decisions/0001-git-precommit-secret-backstop.md`](decisions/0001-git-precommit-secret-backstop.md)
and [TESTING.md](TESTING.md) for the `core.hooksPath` caveat (the absolute pin
breaks if the repo is moved — re-run `install_hooks.sh` after any move).

## Status & scope

This is a personal research/reference repo, not a maintained framework. The
hooks in `hooks/` are copies of one developer's real `~/.claude/hooks/` setup,
lightly genericized for publication — read them as worked examples, not a
library to depend on. Issues and findings from other Claude Code versions are
welcome.

## License

[MIT](LICENSE).
