#!/bin/bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${project_root}/.env.docker"

if [[ ! -f "$env_file" ]]; then
  cp "${project_root}/.env.docker.example" "$env_file"
fi

append_if_missing() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" "$env_file"; then
    printf '%s=%s\n' "$key" "$value" >>"$env_file"
  fi
}

append_if_missing "DBA_MYSQL_ADMIN_PASSWORD" "$(openssl rand -hex 24)"
append_if_missing "DBA_MYSQL_READER_PASSWORD" "$(openssl rand -hex 24)"
append_if_missing "DBA_SESSION_TTL_SECONDS" "300"
append_if_missing "CB_ADMIN_NAME" "cbadmin"
append_if_missing "CB_ADMIN_PASSWORD" "$(openssl rand -hex 24)"

chmod 600 "$env_file"
echo "DBA console secrets are configured in .env.docker (values were not printed)."
