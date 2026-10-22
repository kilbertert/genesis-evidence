#!/usr/bin/env bash
#
# Prepare a genesis-evidence sandbox for an AFK run.
#
# A run's workspace starts empty; this repo's checks are `uv run pytest` plus
# `uv run ruff`, so the venv is what is missing. Idempotent — sandcastle runs
# this once per iteration.
set -euo pipefail

uv sync --extra dev

# The test scheduler, installed into the venv rather than declared in the lockfile.
# `implement.md` runs the suite with `pytest -n auto` (afk 1.7.28): measured on
# AI-Ops, 249.7 s serially vs 46.4 s parallel for the same tests, and the agent
# runs that check on EVERY lap. Not a dev dependency: that rewrites uv.lock, and
# this repository's CD refuses to deploy on a lockfile hash mismatch.
#
# ⚠️ Every run, not once — `uv sync` above removes anything the lockfile does not
# name. Same versions as CI (.github/workflows/ci.yml).
uv pip install "pytest-xdist==3.8.0" "execnet==2.1.2"

