#!/usr/bin/env bash
# Validate the protected production images in a disposable, isolated full stack.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CI_TMP=""

ci_die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

ci_cleanup() {
    local status=$?
    set +e
    if [[ -n "${COMPOSE_CMD+x}" ]]; then
        if [[ "$status" -ne 0 ]]; then
            "${COMPOSE_CMD[@]}" --profile workers ps --all >&2
            "${COMPOSE_CMD[@]}" --profile workers logs --tail=100 >&2
        fi
        "${COMPOSE_CMD[@]}" --profile workers down --volumes --remove-orphans
    fi
    if [[ -e "$PROJECT_ROOT/.env.docker" ]]; then
        unlink "$PROJECT_ROOT/.env.docker"
    fi
    if [[ -n "$CI_TMP" && "$CI_TMP" == /tmp/dwg-agent-ci.* ]]; then
        find "$CI_TMP" -depth -delete
    fi
    return "$status"
}

[[ "${CI:-}" == "true" || "${GITHUB_ACTIONS:-}" == "true" ]] \
    || ci_die "container validation is restricted to CI"
[[ ! -e "$PROJECT_ROOT/.env.docker" ]] \
    || ci_die "refusing to overwrite an existing .env.docker"
trap ci_cleanup EXIT
[[ -n "${CI_COMPOSE_PROJECT:-}" ]] || ci_die "CI_COMPOSE_PROJECT is required"
[[ -n "${CI_HTTP_PORT:-}" ]] || ci_die "CI_HTTP_PORT is required"
command -v docker >/dev/null || ci_die "docker is unavailable"
docker info >/dev/null || ci_die "Docker daemon is unavailable"

python3 "$SCRIPT_DIR/write_env.py" \
    --output "$PROJECT_ROOT/.env.docker" \
    --project "$CI_COMPOSE_PROJECT" \
    --port "$CI_HTTP_PORT"

export DWG_COMPOSE_PROJECT_NAME="$CI_COMPOSE_PROJECT"
source "$PROJECT_ROOT/scripts/lib/compose.sh"
DOCKER_ENV_FILE="$PROJECT_ROOT/.env.docker"
COMPOSE_PROJECT_NAME="$CI_COMPOSE_PROJECT"
COMPOSE_CMD=(
    docker compose
    --project-name "$CI_COMPOSE_PROJECT"
    --project-directory "$PROJECT_ROOT"
    --env-file "$DOCKER_ENV_FILE"
    -f "$PROJECT_ROOT/compose.yaml"
    -f "$PROJECT_ROOT/compose.ci.yaml"
)
source "$PROJECT_ROOT/scripts/release.sh"

compose_require_env
"${COMPOSE_CMD[@]}" --profile workers config --quiet

# Read the fully rendered model instead of trusting file-level interpolation.
mapfile -t ci_volume_names < <(
    "${COMPOSE_CMD[@]}" --profile workers config --format json \
        | python3 -c 'import json, sys; print("\n".join(item.get("name", "") for item in json.load(sys.stdin)["volumes"].values()))'
)
for volume_name in "${ci_volume_names[@]}"; do
    [[ "$volume_name" == "${CI_COMPOSE_PROJECT}_"* ]] \
        || ci_die "non-isolated volume detected: $volume_name"
    case "$volume_name" in
        dwg-agent_app_var|dwg-agent_mysql_data|dwg-agent_minio_data)
            ci_die "formal production volume detected: $volume_name"
            ;;
    esac
done

backend_image=$(compose_env_value DWG_AGENT_IMAGE)
frontend_image=$(compose_env_value DWG_AGENT_FRONTEND_IMAGE)
docker image inspect "$backend_image" >/dev/null \
    || ci_die "prebuilt backend image is missing: $backend_image"
docker image inspect "$frontend_image" >/dev/null \
    || ci_die "prebuilt frontend image is missing: $frontend_image"

# Pull only pinned infrastructure images; application images must remain the
# exact protected artifacts built by the workflow.
"${COMPOSE_CMD[@]}" pull mysql minio
release_verify_protected_image "$backend_image"
release_verify_oda_roundtrip "$backend_image"

CI_TMP=$(mktemp -d /tmp/dwg-agent-ci.XXXXXX)
docker image save -o "$CI_TMP/backend-image.tar" "$backend_image"
python3 "$PROJECT_ROOT/scripts/release/verify_image_archive.py" \
    --archive "$CI_TMP/backend-image.tar" \
    --image "$backend_image"

compose_require_docker_disk_space
"${COMPOSE_CMD[@]}" --profile workers up -d --no-build \
    --force-recreate --remove-orphans
compose_wait_for_healthy_services 360
compose_smoke
compose_verify_storage
"${COMPOSE_CMD[@]}" exec -T backend-api \
    python /app/scripts/release/verify_live_remnant.py \
    --fixture /app/scripts/release/fixtures/oda_runtime_smoke.dxf

# The browser suite uses real login, runtime configuration, MySQL and MinIO.
# Run it only against the isolated production-shaped gateway, never a Vite-only
# preview that would turn backend connectivity into misleading UI failures.
[[ -d "$PROJECT_ROOT/frontend/node_modules" ]] \
    || ci_die "frontend dependencies are missing; run npm ci before validation"
(
    cd "$PROJECT_ROOT/frontend"
    PLAYWRIGHT_FRONTEND_BASE_URL="http://127.0.0.1:${CI_HTTP_PORT}" \
        npx playwright test
)
