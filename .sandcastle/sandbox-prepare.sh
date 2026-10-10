#!/usr/bin/env bash
#
# Prepare a genesis-evidence sandbox for an AFK run.
#
# A run's workspace starts empty; this repo's checks are `uv run pytest` plus
# `uv run ruff`, so the venv is what is missing. Idempotent — sandcastle runs
# this once per iteration.
set -euo pipefail

uv sync --extra dev

