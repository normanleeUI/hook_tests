# Visibility Probe Protocol

Systematic test of what Claude Code surfaces from hooks across event types, tool matchers, and exit codes.

## Questions to answer

| # | Question | Relevant probes |
|---|----------|-----------------|
| Q1 | Does statusMessage show for PreToolUse? | P1, P2 vs P5, P6 |
| Q2 | Does stderr render for Read tool blocks? | P2 vs P4 |
| Q3 | Does exit code affect stderr/stdout visibility? | P1 vs P2, P3 vs P4 |
| Q4 | Does stdout behave differently from stderr? | All (each emits both) |
| Q5 | Does PostToolUse show statusMessage? | P5, P6 |
| Q6 | Is there a Bash-specific rendering path? | P3, P4 vs P1, P2 |

## Probe matrix

All probes use the same script (`probes/visibility_probe.py`), differentiated by `PROBE_ID` env var.

| Probe | Event | Matcher | Exit | statusMessage | Trigger action |
|-------|-------|---------|------|---------------|----------------|
| P1 | PreToolUse | Read | 0 | "PROBE P1: Pre+Read+e0" | "Read README.md" |
| P2 | PreToolUse | Read | 2 | "PROBE P2: Pre+Read+e2" | "Read README.md" |
| P3 | PreToolUse | Bash | 0 | "PROBE P3: Pre+Bash+e0" | "Run echo hello" |
| P4 | PreToolUse | Bash | 2 | "PROBE P4: Pre+Bash+e2" | "Run echo hello" |
| P5 | PostToolUse | Edit | 0 | "PROBE P5: Post+Edit+e0" | Edit any file |
| P6 | PostToolUse | Edit | 2 | "PROBE P6: Post+Edit+e2" | Edit any file |
| P7 | PostToolUse | Bash | 0 | "PROBE P7: Post+Bash+e0" | "Run echo hello" |
| P8 | PostToolUse | Bash | 2 | "PROBE P8: Post+Bash+e2" | "Run echo hello" |

## Setup

### Phase 1: exit-0 probes (safe — all allow)

Wire P1, P3, P5, P7 simultaneously. These all exit 0 so they won't block anything.

**In `~/.claude/settings.json`**, temporarily REPLACE the existing hooks with:

```json
"hooks": {
  "PreToolUse": [
    {
      "matcher": "Read",
      "hooks": [
        {
          "type": "command",
          "command": "PROBE_ID=pre_read_e0 python3 /home/user/projects/hook_tests/probes/visibility_probe.py",
          "statusMessage": "PROBE P1: Pre+Read+e0"
        }
      ]
    },
    {
      "matcher": "Bash",
      "hooks": [
        {
          "type": "command",
          "command": "PROBE_ID=pre_bash_e0 python3 /home/user/projects/hook_tests/probes/visibility_probe.py",
          "statusMessage": "PROBE P3: Pre+Bash+e0"
        }
      ]
    }
  ],
  "PostToolUse": [
    {
      "matcher": "Edit",
      "hooks": [
        {
          "type": "command",
          "command": "PROBE_ID=post_edit_e0 python3 /home/user/projects/hook_tests/probes/visibility_probe.py",
          "statusMessage": "PROBE P5: Post+Edit+e0"
        }
      ]
    },
    {
      "matcher": "Bash",
      "hooks": [
        {
          "type": "command",
          "command": "PROBE_ID=post_bash_e0 python3 /home/user/projects/hook_tests/probes/visibility_probe.py",
          "statusMessage": "PROBE P7: Post+Bash+e0"
        }
      ]
    }
  ]
}
```

### Phase 2: exit-2 probes (one at a time — they block)

Test each exit-2 probe individually. Replace ONE probe at a time with its exit-2 variant.

#### Phase 2a: P2 (Pre+Read+e2)

Replace P1's command with:
```
"command": "PROBE_ID=pre_read_e2 python3 /home/user/projects/hook_tests/probes/visibility_probe.py"
"statusMessage": "PROBE P2: Pre+Read+e2"
```

#### Phase 2b: P4 (Pre+Bash+e2)

Replace P3's command with:
```
"command": "PROBE_ID=pre_bash_e2 python3 /home/user/projects/hook_tests/probes/visibility_probe.py"
"statusMessage": "PROBE P4: Pre+Bash+e2"
```

#### Phase 2c: P6 (Post+Edit+e2)

Replace P5's command with:
```
"command": "PROBE_ID=post_edit_e2 python3 /home/user/projects/hook_tests/probes/visibility_probe.py"
"statusMessage": "PROBE P6: Post+Edit+e2"
```

#### Phase 2d: P8 (Post+Bash+e2)

Replace P7's command with:
```
"command": "PROBE_ID=post_bash_e2 python3 /home/user/projects/hook_tests/probes/visibility_probe.py"
"statusMessage": "PROBE P8: Post+Bash+e2"
```

## Test procedure

Start a **new Claude Code session** for each phase (hooks are read at session start).

### Phase 1 test steps

1. Clear the debug log: `rm -f /tmp/hook_debug.log`
2. Start a new Claude Code session in `hook_tests/`
3. Ask: "Read README.md"
   - Record: Did statusMessage "PROBE P1" flash? Any stderr/stdout visible?
4. Ask: "Run echo hello"
   - Record: Did statusMessage "PROBE P3" flash? Any stderr/stdout visible?
5. Ask: "Edit README.md and add a blank line at the end"
   - Record: Did statusMessage "PROBE P5" flash? Any stderr/stdout visible?
6. Check debug log: `cat /tmp/hook_debug.log`
   - Confirms which probes actually fired
7. Ask: "Run echo done"
   - Record: Did statusMessage "PROBE P7" flash? Any stderr/stdout visible?
   - Also check: did P3 fire again (it matches Bash)?

### Phase 2 test steps (repeat for each sub-phase)

1. Clear debug log
2. Start new session
3. Trigger the relevant action
4. Record:
   - [ ] statusMessage visible?
   - [ ] stderr visible in console?
   - [ ] stdout visible in console?
   - [ ] stdout appears in Claude's next response (system-reminder)?
   - [ ] Action was blocked (Claude shows refusal)?
   - [ ] Block message text visible to user?
5. Check debug log

## Recording template

Copy this for each probe result:

```
### Probe P_: {event}+{matcher}+e{code}
- statusMessage visible: YES / NO / FLASH-ONLY
- stderr visible in console: YES / NO
- stdout visible in console: YES / NO
- stdout in Claude's response: YES / NO
- Action blocked (e2 only): YES / NO
- Block message visible: YES / NO / PARTIAL
- Debug log confirms fired: YES / NO
- Notes:
```

## Phase 2 probe matrix (P9–P13)

These probes validate assumptions that the channel redesign fix strategies depend on.

All P9/P10/P11/P13 use `probes/channel_probe.py`, differentiated by `PROBE_ID` and `PROBE_EXIT` env vars.
P12 uses `probes/hookspecific_probe.py`.

Each probe logs full stdin JSON to `/tmp/probe_input_<id>.json` (or `PROBE_DUMP_DIR` if set).

| Probe | Event | Matcher | Exit | Purpose |
|-------|-------|---------|------|---------|
| P9 | PreToolUse | Edit | 2 | Validate Strategy A |
| P10 | PreToolUse | Write | 2 | Validate Strategy A |
| P11 | PreToolUse | WebFetch | 2 | Validate Strategy C guard |
| P12 | PostToolUse | Edit | 0 | Test hookSpecificOutput.additionalContext |
| P13 | PreToolUse | Bash | 2 | Test `if:` condition filtering (with `if: Bash(*probe*)`) |

### Step 0c Experiments

| Experiment | Question | Method |
|---|---|---|
| A | Do PostToolUse hooks in the same group run sequentially? Does a blocked hook stop later hooks? | Edit a .py file with `# type: ignore` (triggers block_suppressions, exit 2) AND bad formatting (triggers ruff_format). Check: does ruff reformat? Does block_suppressions still block? |
| B | Do subsequent hooks see prior hooks' file modifications? | Edit a .py file with a type error on a line ruff would reformat. After edit, check: did pyright report the original or reformatted line number? |

## Teardown

After all tests, restore the original `~/.claude/settings.json`. A backup should be saved before starting:

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak
```
