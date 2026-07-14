# Claude Code Hooks: Findings & Test Harness

A worked investigation into **Claude Code hooks** — which of their output
channels actually reach you and the model, which silently do nothing, and how
to test hooks so you find out *before* you rely on one. It ships:

- **A reference table of empirically-verified hook channel behavior** (below) —
  the reusable takeaway, even if you never run the code.
- **~30 real hook scripts** in [`hooks/`](hooks/) (security guards, linters,
  injection scanners) you can read or adapt.
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

### Behavioral gotchas that cost real debugging time

- **PostToolUse hooks in the same group run in *parallel*, not sequentially.**
  Two hooks that read-modify-write the same file will race, and the last writer
  wins — findings silently vanish. Fix: `fcntl.flock` on a shared
  `<filepath>.hook_lock`, held only during the read-modify-write phase. (We
  first concluded "sequential" from a fast/slow hook pair — a good lesson in
  probe design; see the addendum in [`probes/PROBE_RESULTS_PHASE2.md`](probes/PROBE_RESULTS_PHASE2.md).)
- **PostToolUse exit-2 is cosmetic.** The tool already ran; exit 2 only informs
  the model. To actually prevent an action, you must be on **PreToolUse**.
- **The `if:` field does not support `|` OR syntax** (unlike `matcher`).
  `"if": "Bash(*uv add*)|Bash(*uv sync*)"` matches nothing and silently disables
  the hook. Use one condition, or guard inside the script.
- **`hookEventName` is required inside `hookSpecificOutput`** but undocumented —
  omit it and the whole output is discarded.

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
hooks/                 # ~30 vendored hook scripts (the implementations under test)
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
