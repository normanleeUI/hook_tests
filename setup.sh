#!/usr/bin/env bash
set -euo pipefail

# Create venv and install dev dependencies
uv venv .venv
uv sync --extra dev
