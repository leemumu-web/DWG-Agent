#!/usr/bin/env bash
# DWG-Agent — stable Docker Compose deployment command facade.
set -Eeuo pipefail
source "$(dirname "$0")/lib/compose.sh"

compose_main "$@"
