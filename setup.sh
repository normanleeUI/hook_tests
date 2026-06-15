#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || uv venv
uv sync --extra dev
touch .last_dep_check
git add .gitignore README.md TESTING.md pyproject.toml src/ tests/ fixtures/ .env.example setup.sh .github/
git add --force .claude/settings.json
git commit -m "Initial commit: hook test harness" || true
echo "Setup complete."
echo "  Automated tests: pytest tests/test_hooks/ -v"
echo "  Interactive tests: open a new Claude Code session and follow TESTING.md"
