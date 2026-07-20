#!/usr/bin/env bash
# Stable command facade for one queue-scoped CAD Celery worker.
set -euo pipefail
source "$(dirname "$0")/lib/cad_worker.sh"

cad_worker_main "$@"
