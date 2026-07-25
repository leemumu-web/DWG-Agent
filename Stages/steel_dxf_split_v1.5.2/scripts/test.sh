#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --extra dev
uv run python ./scripts/run_test_matrix.py
