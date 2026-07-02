#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"
uv run python -m app.db.init_db
