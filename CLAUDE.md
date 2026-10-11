# hook_tests — project instructions

Test harness for the Claude Code hooks that live in `~/.claude/hooks/`, which
are version-controlled in `../../../shared_resources/claude-config/hooks/`.
This repo holds no hook code of its own. The suite imports and runs the
**live** hooks (`HOOKS_DIR`, default `~/.claude/hooks`), so what it tests is
what is deployed.

Working rules:
- **Paired edits.** A hook logic change in claude-config needs its test change
  here, in the same session. Tests for a hook that is not yet deployed skip
  until `install.sh` ships it, then run on their own. A skip is not a pass:
  re-run after the deploy.
- **Run:** `uv sync --extra dev` once (pytest lives in the `dev` extra), then
  `uv run pytest -q`. To test a repo copy before deploying it, set
  `HOOKS_DIR=../../../shared_resources/claude-config/hooks`.
- **No CI.** It was removed on 2026-10-06 because, without a live
  `~/.claude`, it skipped about 95% of the suite. The local run is the real
  coverage.
- **Wiring vs logic.** `tests/test_hooks/` checks hook decisions.
  `TESTING.md` is the manual playbook for whether Claude Code actually fires a
  hook. Findings about which output channels work are in `README.md` and
  `probes/`.
- Audit history and decisions live in claude-config's `docs/LEDGER.md`, not
  here. The ADRs specific to this repo are in `decisions/`.
