# Batch 2 — Agent Mode

An aggregated alternative to the one-command-at-a-time Batch 2 in `TESTING.md`.
Instead of driving each command as its own prompt, you paste **three** prompts
into a live Claude Code session (in `hook_tests/`), then verify the results from
the debug log with `scripts/check_batch2.py`.

**Why this is safe now:** verification does not trust the agent's narration.
Every hook logs the triggering `cmd=` and its `BLOCK`/`ALLOW` decision with a
`cwd=` tag (see `hook_log.py`). The checker reads that log, filtered to this
project, and asserts the wiring truth table. If the agent alters, reorders, or
skips a command, that shows up as a `FAIL`/`MISSING` line — it can't pass
silently. This only works in a **live Claude Code session**, where PreToolUse
hooks actually fire.

---

## Preconditions

Run in your normal terminal, from `hook_tests/`:

```bash
./scripts/verify_prerequisites.sh          # environment sane
cp fixtures/staged_secret.py src/staged_secret_test.py   # realistic fake keys
git add src/staged_secret_test.py          # scan_secrets precondition (Batch 0)
rm -f .hook_state/pip_audit/report.json     # clean pip_audit state (see 2.11 note)
./scripts/observe.sh --reset               # clear the debug log for a fresh run
```

Do **not** `--reset` again between groups — run A, B, C into one log, then check
once at the end.

---

## Shared execution contract

Each prompt below begins with this contract. Keep it verbatim — it's what keeps
the agent from collapsing or bypassing tests.

> You are running a hook-wiring test batch. For EACH command in the numbered
> list below, in order:
> 1. Issue it as its OWN single Bash tool call, EXACTLY as written — same
>    characters, same quoting. A command may itself contain `&&` or quotes;
>    that is part of the test, so run it as one call, do not split or reformat.
> 2. Do NOT combine, reorder, skip, or add commands. Run ONLY what is listed.
> 3. Many commands are SUPPOSED to be blocked by a hook — that is the expected
>    result, not an error. When one is blocked, do NOT try to work around it
>    (no alternate command, no renaming, no bypass). Note it and move on.
> 4. Do not fix anything or interpret results. Run the list top to bottom, then
>    stop. Verification happens separately from the log.

---

## Prompt A — read barrage (`block_read_env`)

Paste the contract above, then:

```
1. cat .env
2. head -5 .env.production
3. base64 .env
4. source .env
5. python3 -c 'open(".env").read()'
6. echo hello && cat .env
7. cat .env.example
8. cat .env.template
9. cat README.md
```

Expected: 1–6 BLOCK, 7–9 ALLOW.

---

## Prompt B — bare-pip + git-add (`block_bare_pip`, `block_git_add_env`)

> ⚠️ These mutate git staging, and command 2 (`uv pip install requests`) will
> actually install a package if allowed. Both are expected; cleanup below.

Paste the contract, then:

```
1. pip install requests
2. uv pip install requests
3. git status
4. git add .env
5. git add .
6. git add src/clean_module.py
7. git status
```

Expected: 1 BLOCK, 2 ALLOW (runs), 3 ALLOW, 4–5 BLOCK, 6 ALLOW, and for cmd 7
`block_git_add_env` does NOT fire (`if: git add*` filters it).

---

## Group C safety guard — RUN THIS FIRST (normal terminal)

Group C issues `git commit` three times with the fake-secret fixture staged.
`scan_secrets_on_commit` **blocks** all three (verified 2026-07-02), so no commit
should land. This guard is belt-and-suspenders — it undoes any commit that slips
through if the fixture or hook ever regresses:

```bash
git rev-parse HEAD > .batch2_head          # remember where HEAD is
```

## Prompt C — cross-cutting + `if:` filtering + `scan_secrets`

Paste the contract, then:

```
1. pip install requests
2. git add .env
3. git commit -m 'test'
4. git commit -m 'test wiring'
5. git status
6. git add .
7. echo hello
8. git commit -m 'secret test'
```

Expected: the co-firing / `if:`-filter table in `check_batch2.py` (group C).
`scan_secrets_on_commit` BLOCKS the commits (the fixture's key is staged); it
does NOT fire on `git status`. `block_git_add_env` fires on `git add .` but NOT
on `echo hello`.

---

## Verify

```bash
python3 scripts/check_batch2.py            # all groups
python3 scripts/check_batch2.py A          # or one group at a time
```

`✓ PASS` / `✗ FAIL` per expectation; nonzero exit if anything needs attention.
To eyeball the raw record (decisions the checker treats as pass-either-way, e.g.
whether `scan_secrets` blocked or allowed): `./scripts/observe.sh --all`.

---

## Cleanup (normal terminal)

```bash
git reset --soft "$(cat .batch2_head)" 2>/dev/null   # undo any test commits, keep changes
rm -f .batch2_head
git restore --staged src/staged_secret_test.py 2>/dev/null
rm -f src/staged_secret_test.py
uv pip uninstall requests 2>/dev/null                # from Prompt B cmd 2
./scripts/observe.sh --reset
```

---

## Caveats — what this does and does NOT cover

- **Wiring truth table only.** The checker proves each hook's decision per
  command. It is not a proof that the agent issued every command: four commands
  repeat across groups (`pip install requests`, `git add .env`, `git add .`,
  `git status`), so a skipped instance of a repeated command is masked by its
  twin. A real regression fails *all* instances, so wiring conclusions hold.
- **Exact quoting matters.** The checker matches the logged `cmd=` string
  exactly. If the agent reformats a command (e.g. changes the quoting on the
  `python3 -c` line), that row reads `MISSING` — inspect, don't assume broken.
- **UI-rendering checks are not covered.** The "red dot, no stderr" behaviour of
  Read-tool blocks (Batch 3) and stderr visibility are human-eyeball facts,
  already recorded in the Observation Guide — not re-verified here.
- **`>60` char commands** are truncated with `…` in the log; none in Batch 2 hit
  that, but a future command that does won't match exactly.
