"""Suite-wide fixtures.

[n4] Many test files create temp git repos and run real `git commit` without
pinning core.hooksPath, so the LIVE global pre-commit hook fires and appends
to the live ledger ~/.claude/logs/precommit.log. Redirect it for the whole
suite. PRECOMMIT_LEGS is inert against the current hook but pre-set for the
Step 3 knob.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_precommit_ledger(tmp_path_factory):
    # Session scope == once per xdist worker; tmp_path_factory is per-worker.
    os.environ["PRECOMMIT_LOG"] = str(
        tmp_path_factory.mktemp("precommit") / "ledger.log"
    )
    os.environ["PRECOMMIT_LEGS"] = ""
