#!/usr/bin/env bash
# DWG-Agent — stable MySQL runtime command facade.
set -euo pipefail
source "$(dirname "$0")/lib/database.sh"

db_main "$@"
