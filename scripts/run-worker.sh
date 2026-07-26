#!/usr/bin/env bash
# Container worker entrypoint: wait for MySQL, then preserve the worker as PID 1.
set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "用法: $0 <worker-command> [args...]" >&2
    exit 2
fi

python -m app.platform.database.wait
exec "$@"
