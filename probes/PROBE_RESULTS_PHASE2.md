# Phase 2 Probe Results

Recorded during manual testing of P9–P13 probes (channel redesign Step 1).

## Probe P9: PreToolUse+Edit+e2

- statusMessage visible: UNKNOWN (not explicitly noted — likely flashed briefly)
- stderr visible in console: YES (`PROBE pre_edit_e2: stderr test`)
- stdout visible in console: NO (not shown in interaction)
- stdout in Claude's response: NO
- Action blocked (e2 only): YES (Edit was blocked, error shown)
- Block message visible: YES (full stderr line shown in error: `PROBE pre_edit_e2: stderr test`)
- Debug log confirms fired: YES (block triggered, JSON dumped)
- Input JSON structure: `tool_input` contains `file_path`, `old_string`, `new_string`, `replace_all` (confirms AC-PRB-05)
- Notes: Claude saw the block and reported "The edit was blocked by a hook probe." The tool_input JSON includes session_id, transcript_path, cwd, permission_mode, effort, hook_event_name, tool_name, tool_use_id in addition to tool_input. Strategy A (PreToolUse promotion) is validated for Edit.

## Probe P10: PreToolUse+Write+e2

### Attempt 1 (FAILED — settings.json typo)
- Result: Script crashed (exit 1, non-blocking) because command had `2vpython3` instead of `2 python3` — missing space from copy-paste.

### Attempt 2 (SUCCESS)
- statusMessage visible: UNKNOWN (not explicitly noted)
- stderr visible in console: YES (`PROBE pre_write_e2: stderr test`)
- stdout visible in console: NO (not shown in interaction)
- stdout in Claude's response: NO
- Action blocked (e2 only): YES (Write was blocked, error shown)
- Block message visible: YES (full stderr line shown in error: `PROBE pre_write_e2: stderr test`)
- Debug log confirms fired: YES (no crash log, input JSON written)
- Input JSON structure: `tool_input` contains `file_path`, `content` (confirms AC-PRB-06)
- Notes: Strategy A (PreToolUse promotion) is validated for Write. Claude reported "The write was blocked by a pre-write hook."

## Probe P11: PreToolUse+WebFetch+e2

- statusMessage visible: UNKNOWN (not explicitly noted)
- stderr visible in console: YES (`PROBE pre_webfetch_e2: stderr test`)
- stdout visible in console: NO (not shown in interaction)
- stdout in Claude's response: NO
- Action blocked (e2 only): YES (WebFetch was blocked, error shown)
- Block message visible: YES (full stderr line shown in error: `PROBE pre_webfetch_e2: stderr test`)
- Debug log confirms fired: YES (block triggered)
- Input JSON structure: NOT YET CHECKED (need to cat /tmp/probe_input_pre_webfetch_e2.json)
- Notes: Strategy C guard (PreToolUse promotion) is validated for WebFetch. Claude reported the block and offered to help disable the hook. The tool was displayed as "Fetch" in the UI, matched by "WebFetch" in the settings.

## Probe P12: PostToolUse+Edit+e0 (hookSpecificOutput)

### Attempt 1 (PARTIAL)
- Hook fired, but Claude Code rejected the output with: "hookSpecificOutput is missing required field 'hookEventName'"
- Discovery: hookSpecificOutput requires `hookEventName` field in addition to `additionalContext`. This was undocumented.
- Fixed probe to include `hookEventName` sourced from the input payload's `hook_event_name`.

### Attempt 2 (SUCCESS — observed live in current session)
- Hook fired on every Edit in the active session (P12 was still wired in PostToolUse+Edit)
- additionalContext reached the model: appeared as `<system-reminder>PostToolUse:Edit hook additional context: PROBE HSO: This text should reach the model</system-reminder>`
- Fired consistently on every Edit (observed 4 consecutive firings)
- AC-PRB-04 CONFIRMED: hookSpecificOutput.additionalContext is a viable channel for delivering information to the model
- Key discovery: the format Claude Code delivers it is `PostToolUse:Edit hook additional context: <value>` inside a system-reminder tag
- Notes: This confirms the hookSpecificOutput channel works for PostToolUse hooks. The required `hookEventName` field was not documented in the plan — probe testing discovered this requirement empirically.

## Probe P13: PreToolUse+Bash+e2 (if condition filtering)

- Matching command (`echo probe_test`): BLOCKED — hook fired, stderr visible (`PROBE if_cond_e2: stderr test`), action blocked
- Non-matching command (`echo hello`): ALLOWED — hook did NOT fire, command executed normally, output `hello`
- AC-PRB-07 CONFIRMED: `if:` condition `Bash(*probe*)` correctly filters — hook fires only when the command matches the glob pattern
- Notes: The `if:` condition uses glob-style matching. `Bash(*probe*)` matches any Bash command containing "probe". This confirms `if:` conditions can be used to selectively fire hooks on specific command patterns (Strategy C guard is viable for conditional hooks).

## Experiment A: Do PostToolUse hooks run sequentially? Does a blocked hook stop later hooks?

- **Hooks run sequentially**: YES — ruff_format (hook #2 in the Edit|Write group) ran before block_suppressions (hook #7). Evidence: the edit applied `print(    'hello'    ) # type: ignore` but Claude's subsequent file read showed `print("hello")  # type: ignore` (ruff reformatted spacing and quotes).
- **Blocked hook stops later hooks**: NO — block_suppressions exited 2 (blocked), but ruff_format had already run (it's earlier in the hook list). More importantly, the block did NOT revert the edit or ruff_format's changes. The file persisted with all modifications.
- **PostToolUse exit-2 is cosmetic**: CONFIRMED — the edit was already applied (it's PostToolUse, not PreToolUse), and exit-2 only informs Claude that something was wrong. Claude then adjusted its approach, but the file had already been modified by the edit + ruff_format.
- AC-PRB-08 CONFIRMED: PostToolUse hooks in the same group run sequentially in the order listed in settings.json.
- Hook execution order observed: block_glob_deny_rules → ruff_format → pyright_check → check_docstrings → check_dependency_pins → check_random_seeds → block_suppressions → bandit_check.

## Experiment B: Do subsequent hooks see prior hooks' file modifications?

- **Subsequent hooks see prior modifications**: YES — ruff_format modified the file on disk (reformatted spacing and quotes), and the file persisted with those changes. When Claude re-read the file after the block, it saw ruff's reformatted version (`print("hello")` not `print(    'hello'    )`), confirming the file was modified between hooks.
- AC-PRB-09 CONFIRMED: Hooks operate on the actual file on disk, not on a snapshot. Each hook sees all prior hooks' file modifications.
- Note: This was inferred from the ruff_format → file state chain rather than the original plan's pyright line-number test, but the evidence is equally conclusive — the file on disk was demonstrably changed by ruff_format and subsequent reads reflected those changes.

## Addendum (2026-06-22): Experiments A/B conclusions partially wrong — hooks run in parallel

### What we got wrong

AC-PRB-08 ("PostToolUse hooks run sequentially") was overstated. The evidence
showed ruff_format's output was visible when later hooks read the file, but
this is equally consistent with **parallel execution where a fast hook finishes
before slower hooks reach their read phase**. The probe did not test with two
slow hooks that would expose a race condition.

### How we discovered it

During manual testing of bandit_check.sh (Strategy B inline injection), the
`# HOOK:BANDIT:` comment was consistently missing from edited files even though
`# HOOK:PYRIGHT:` appeared. Debug logging in `inject_tool_findings.py` revealed:

1. Both pyright and bandit fired, both found findings, both wrote to the file.
2. Both read the **original** file (without each other's comments) — proving
   they started before either had written.
3. The last writer won: pyright (slower due to nodeenv startup) overwrote
   bandit's changes, or vice versa depending on timing.

This is a classic read-modify-write race condition that only manifests when two
hooks take comparable time — explaining why ruff_format (sub-50ms) appeared
sequential in the original probe.

### Corrected conclusions

- **AC-PRB-08 REVISED**: PostToolUse hooks in the same group run **in parallel**,
  not sequentially. All hooks receive the same event payload on stdin, but they
  execute as concurrent subprocesses.
- **AC-PRB-09 STANDS (with caveat)**: Hooks operate on the real file on disk,
  not a snapshot. But because hooks run in parallel, a hook only sees prior
  modifications if the writing hook happened to finish first — there is no
  ordering guarantee.
- The observed "execution order" (line 73 above) likely reflects log-write
  timing, not a causal sequence.

### Fix applied

Added `fcntl.flock`-based file locking to all PostToolUse Edit|Write hooks
that write to the edited file:

| Hook | Locking mechanism |
|---|---|
| `ruff_format.sh` | `flock "$f.hook_lock"` (bash) |
| `pyright_check.sh` | `fcntl.flock` in `inject_tool_findings.py` |
| `check_docstrings.py` | `fcntl.flock` in `hook_inject.read_clean_write()` |
| `check_random_seeds.py` | `fcntl.flock` in `hook_inject.read_clean_write()` |
| `bandit_check.sh` | `fcntl.flock` in `inject_tool_findings.py` |

All use the same lock file (`<filepath>.hook_lock`), so writes serialize
correctly across hooks regardless of execution order. The lock is held only
during the read-modify-write phase — slow operations (running pyright, bandit)
happen outside the lock.

No other hook groups are affected: PreToolUse hooks don't modify files, and the
PostToolUse Bash hook (`pip_audit_check.py`) writes to `.hook_state/`, not to
the edited file.

### Additional discovery: `if` field does not support `|` OR syntax

During the same debugging session, we found that the `if` field in hook
configuration does not support `|` as an OR separator (unlike `matcher`).
The pattern `"if": "Bash(*uv add*)|Bash(*uv sync*)|Bash(*uv pip install*)"`
was treated as a single literal pattern and matched nothing. Both
`pip_audit_guard.py` and `pip_audit_check.py` were silently never firing.

Fix: removed the `if` field entirely. Both hooks already have internal
command matching that handles the filtering correctly.
