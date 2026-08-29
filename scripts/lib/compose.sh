#!/usr/bin/env bash
# Docker Compose deployment, backup and restore implementation.

if [ "${DWG_COMPOSE_LIB_LOADED:-0}" = "1" ]; then
    return 0
fi
DWG_COMPOSE_LIB_LOADED=1

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

DOCKER_ENV_FILE="$PROJECT_ROOT/.env.docker"
COMPOSE_PROJECT_NAME="${DWG_COMPOSE_PROJECT_NAME:-dwg-agent}"
# $DOCKER_BIN resolves to "docker" or "sudo docker" (see common.sh) so the
# same commands work on hosts where the daemon socket needs sudo.
COMPOSE_CMD=(
    $DOCKER_BIN compose
    --project-name "$COMPOSE_PROJECT_NAME"
    --project-directory "$PROJECT_ROOT"
    --env-file "$DOCKER_ENV_FILE"
)

compose_usage() {
    cat <<'EOF'
Usage: bash scripts/docker.sh <command>

Commands:
  check       Validate prerequisites, secrets, Compose, and tracked source
  build       Build backend and frontend images
  up          Start the core stack
  up-workers  Start the core stack and conversion workers
  status      Show containers and health
  logs        Follow service logs
  smoke       Check Nginx and backend readiness through the public port
  verify-storage
              Verify a real MySQL-registered MinIO write/read/delete transaction
  down        Stop containers while preserving data volumes
  backup DIR  Back up MySQL and MinIO into DIR
  restore DIR Restore a stopped stack from a backup created by this script
EOF
}

compose_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
compose_info() { printf '==> %s\n' "$*"; }
compose_warn() { printf 'WARNING: %s\n' "$*" >&2; }

compose_env_value() {
    local key=$1
    awk -F= -v expected="$key" '
        $1 == expected { value = substr($0, index($0, "=") + 1) }
        END { sub(/\r$/, "", value); print value }
    ' "$DOCKER_ENV_FILE"
}

compose_require_production_features() {
    local app_env
    app_env=$(compose_env_value APP_ENV)
    app_env=${app_env,,}
    [[ "$app_env" == "production" || "$app_env" == "prod" ]] || return 0

    local -a expected=(
        "DXF_PIPELINE_ENABLED=true"
        "DXF2DWG_PIPELINE_ENABLED=true"
        "DXF2EXCEL_PIPELINE_ENABLED=false"
        "DXF_CLASSIFICATION_PIPELINE_ENABLED=true"
        "DXF_SPLIT_PIPELINE_ENABLED=true"
        "EXCEL_FINAL_PIPELINE_ENABLED=true"
        "REMNANT_INVENTORY_ENABLED=true"
    )
    local item key wanted actual
    for item in "${expected[@]}"; do
        key=${item%%=*}
        wanted=${item#*=}
        actual=$(compose_env_value "$key")
        actual=${actual,,}
        [[ "$actual" == "$wanted" ]] \
            || compose_die "$key must be $wanted in the production .env.docker"
    done
}

compose_require_env() {
    [[ -f "$DOCKER_ENV_FILE" ]] || compose_die "missing .env.docker; copy .env.docker.example and replace placeholders"
    if grep -Eq '^[A-Za-z_][A-Za-z0-9_]*=CHANGE_ME_' "$DOCKER_ENV_FILE"; then
        compose_die ".env.docker still contains CHANGE_ME_* placeholders"
    fi
    local required=(MYSQL_PASSWORD MYSQL_ROOT_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD JWT_SECRET_KEY SUPER_ADMIN_PASSWORD)
    local key
    for key in "${required[@]}"; do
        grep -Eq "^${key}=.+" "$DOCKER_ENV_FILE" || compose_die "$key is missing or empty in .env.docker"
    done
    compose_require_production_features
}

compose_require_docker_disk_space() {
    local minimum_gib docker_root available_kib available_gib
    minimum_gib=$(compose_env_value DOCKER_MIN_FREE_GIB)
    minimum_gib=${minimum_gib:-20}
    [[ "$minimum_gib" =~ ^[1-9][0-9]*$ ]] \
        || compose_die "DOCKER_MIN_FREE_GIB must be a positive integer"

    docker_root=$($DOCKER_BIN info --format '{{.DockerRootDir}}') \
        || compose_die "cannot determine the Docker data root"
    [[ -n "$docker_root" ]] || compose_die "Docker returned an empty data root"
    available_kib=$(df -Pk -- "$docker_root" | awk 'END { print $4 }') \
        || compose_die "cannot determine free space for Docker data root: $docker_root"
    [[ "$available_kib" =~ ^[0-9]+$ ]] \
        || compose_die "Docker data-root free-space result is invalid"
    if (( available_kib < minimum_gib * 1024 * 1024 )); then
        available_gib=$((available_kib / 1024 / 1024))
        compose_die "Docker data root $docker_root has ${available_gib} GiB free; deployment requires at least ${minimum_gib} GiB free"
    fi
    available_gib=$((available_kib / 1024 / 1024))
    compose_info "Docker data root $docker_root: ${available_gib} GiB available"
}

compose_check_source() {
    [[ -f "$PROJECT_ROOT/Stages/dxf2excel/pyproject.toml" ]] || compose_die "Stages/dxf2excel source is absent"
    if git -C "$PROJECT_ROOT" ls-files -s Stages/dxf2excel | grep -q '^160000 '; then
        compose_warn "Stages/dxf2excel is an unreproducible gitlink; this checkout can build, but a clean clone cannot."
    fi
}

compose_check() {
    command -v docker >/dev/null || compose_die "docker is not installed"
    $DOCKER_BIN info >/dev/null || compose_die "Docker daemon is unavailable"
    compose_require_env
    compose_require_docker_disk_space
    compose_check_source
    "${COMPOSE_CMD[@]}" config --quiet
    "${COMPOSE_CMD[@]}" --profile workers config --quiet
    compose_info "Docker deployment checks passed"
}

compose_public_port() {
    local value
    value=$(grep -E '^HTTP_PORT=' "$DOCKER_ENV_FILE" | tail -n1 | cut -d= -f2- || true)
    printf '%s' "${value:-80}"
}

compose_smoke() {
    compose_require_env
    local port runtime_status=0
    port=$(compose_public_port)
    if ! curl -fsS "http://127.0.0.1:${port}/nginx-health" >/dev/null; then
        compose_warn "public gateway health check failed"
        return 1
    fi
    if ! curl -fsS "http://127.0.0.1:${port}/health/ready" >/dev/null; then
        compose_warn "backend readiness check failed"
        return 1
    fi
    compose_info "public gateway and backend readiness checks passed"
    "${COMPOSE_CMD[@]}" exec -T backend-api \
        python /app/scripts/release/verify_runtime_features.py || runtime_status=$?
    if [ "$runtime_status" -ne 0 ]; then
        compose_warn "production runtime feature verification failed"
        return "$runtime_status"
    fi
    compose_info "production runtime feature matrix passed"
}

compose_verify_storage() {
    compose_require_env
    local backend_state verify_username verify_password
    if ! backend_state="$("${COMPOSE_CMD[@]}" ps --all \
        --format '{{.Service}}|{{.State}}|{{.Health}}' backend-api)"; then
        compose_die "cannot inspect backend-api before storage verification"
    fi
    if ! grep -qx 'backend-api|running|healthy' <<<"$backend_state"; then
        compose_die "backend-api must be running and healthy before storage verification"
    fi

    compose_info "verifying the registered MySQL and object-storage transaction path"
    local probe_status=0
    verify_username=$(compose_env_value VERIFY_ADMIN_USERNAME)
    verify_password=$(compose_env_value VERIFY_ADMIN_PASSWORD)
    if { [[ -n "$verify_username" ]] && [[ -z "$verify_password" ]]; } \
        || { [[ -z "$verify_username" ]] && [[ -n "$verify_password" ]]; }; then
        compose_die "VERIFY_ADMIN_USERNAME and VERIFY_ADMIN_PASSWORD must be configured together"
    fi
    if [[ -n "$verify_username" ]]; then
        VERIFY_ADMIN_USERNAME="$verify_username" VERIFY_ADMIN_PASSWORD="$verify_password" \
            "${COMPOSE_CMD[@]}" exec -T \
            -e VERIFY_ADMIN_USERNAME -e VERIFY_ADMIN_PASSWORD \
            backend-api python /app/scripts/storage/verify_transactions.py \
            || probe_status=$?
    else
        "${COMPOSE_CMD[@]}" exec -T backend-api \
            python /app/scripts/storage/verify_transactions.py || probe_status=$?
    fi
    if [ "$probe_status" -ne 0 ]; then
        compose_warn "storage transaction verification failed"
        return "$probe_status"
    fi
    compose_info "storage transaction verification passed"
}

compose_startup_diagnostics() {
    local -a affected_services=("$@")
    compose_warn "full-stack startup did not reach a healthy state"
    "${COMPOSE_CMD[@]}" --profile workers ps --all >&2 || true
    if [ "${#affected_services[@]}" -gt 0 ]; then
        "${COMPOSE_CMD[@]}" --profile workers logs --tail=80 \
            "${affected_services[@]}" >&2 || true
    fi
}

compose_wait_for_healthy_services() {
    local timeout="${1:-180}"
    local deadline=$((SECONDS + timeout))
    local expected_output rows_output service state health item
    local terminal healthy_count
    local -a expected rows affected_labels affected_services
    declare -A states=() health_states=() seen=()

    if ! expected_output="$("${COMPOSE_CMD[@]}" --profile workers config --services)"; then
        compose_warn "failed to read expected Compose services"
        return 1
    fi
    mapfile -t expected <<<"$expected_output"
    if [ "${#expected[@]}" -eq 0 ] || [ -z "${expected[0]}" ]; then
        compose_warn "Compose returned no expected services"
        return 1
    fi

    while true; do
        if ! rows_output="$("${COMPOSE_CMD[@]}" --profile workers ps --all \
            --format '{{.Service}}|{{.State}}|{{.Health}}')"; then
            compose_warn "failed to inspect Compose service state"
            return 1
        fi
        mapfile -t rows <<<"$rows_output"
        states=()
        health_states=()
        seen=()
        for item in "${rows[@]}"; do
            [ -n "$item" ] || continue
            IFS='|' read -r service state health <<<"$item"
            [ -n "$service" ] || continue
            states["$service"]="$state"
            health_states["$service"]="$health"
            seen["$service"]=1
        done

        terminal=false
        healthy_count=0
        affected_labels=()
        affected_services=()
        for service in "${expected[@]}"; do
            if [ -z "${seen[$service]+x}" ]; then
                affected_labels+=("${service}=missing")
                continue
            fi
            state="${states[$service]}"
            health="${health_states[$service]}"
            if [ "$state" = "running" ] \
                && { [ -z "$health" ] || [ "$health" = "healthy" ]; }; then
                healthy_count=$((healthy_count + 1))
                continue
            fi
            affected_labels+=(
                "${service}=state:${state:-unknown},health:${health:-none}"
            )
            affected_services+=("$service")
            case "$state:$health" in
                restarting:*|exited:*|dead:*|removing:*|running:unhealthy)
                    terminal=true
                    ;;
                created:*|running:starting)
                    ;;
                *)
                    terminal=true
                    ;;
            esac
        done

        if [ "$healthy_count" -eq "${#expected[@]}" ]; then
            compose_info "${healthy_count} services healthy"
            return 0
        fi
        if $terminal; then
            printf 'ERROR: service not ready: %s\n' "${affected_labels[@]}" >&2
            compose_startup_diagnostics "${affected_services[@]}"
            return 1
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            printf 'ERROR: startup timed out: %s\n' "${affected_labels[@]}" >&2
            compose_startup_diagnostics "${affected_services[@]}"
            return 1
        fi
        sleep 2
    done
}

compose_up_workers() {
    compose_check
    "${COMPOSE_CMD[@]}" --profile workers up -d --build --force-recreate \
        --remove-orphans
    compose_wait_for_healthy_services 180
    compose_smoke
}

compose_service_container_id() {
    local service=$1 container_id
    container_id=$("${COMPOSE_CMD[@]}" ps -aq "$service")
    [[ -n "$container_id" ]] || compose_die "Compose service has no container: $service"
    printf '%s' "$container_id"
}

compose_service_image_id() {
    local service=$1 container_id image_id
    container_id=$(compose_service_container_id "$service")
    image_id=$($DOCKER_BIN inspect --format '{{.Image}}' "$container_id")
    [[ -n "$image_id" ]] || compose_die "Compose service has no image: $service"
    printf '%s' "$image_id"
}

compose_backup() {
    local destination=${1:-}
    [[ -n "$destination" ]] || compose_die "backup requires a destination directory"
    compose_require_env
    mkdir -p "$destination"
    destination=$(cd "$destination" && pwd)

    local storage_backend
    storage_backend=$(grep -E '^STORAGE_BACKEND=' "$DOCKER_ENV_FILE" | tail -n1 | cut -d= -f2- || echo "minio")

    local backup_start backup_end backend_container backend_image
    backup_start=$(date -u +%s)
    backend_container=$(compose_service_container_id "backend-api")
    backend_image=$(compose_service_image_id "backend-api")

    compose_info "creating consistent MySQL dump"
    "${COMPOSE_CMD[@]}" exec -T mysql sh -c \
        'exec env -u MYSQL_HOST -u MYSQL_PORT mysqldump -S "$MYSQL_UNIX_PORT" -u root -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --events --triggers --databases "$MYSQL_DATABASE" hardware_handbook' \
        | gzip -9 > "$destination/mysql.sql.gz"

    if [[ "$storage_backend" == "minio" ]]; then
        compose_info "archiving MinIO object data"
        local minio_container
        minio_container=$(compose_service_container_id "minio")
        $DOCKER_BIN run --rm --network none --read-only --user 0:0 \
            --volumes-from "$minio_container":ro --entrypoint sh "$backend_image" -c \
            'cd /data && tar -czf - .' > "$destination/minio-data.tar.gz"
    else
        compose_warn "STORAGE_BACKEND=$storage_backend — MinIO archive skipped; objects in app_var volume"
        compose_info "archiving app_var/storage (local backend)"
        $DOCKER_BIN run --rm --network none --read-only --user 0:0 \
            --volumes-from "$backend_container":ro --entrypoint sh "$backend_image" -c \
            'cd /app/var && tar -czf - storage' > "$destination/app-var-storage.tar.gz" 2>/dev/null || \
            compose_warn "app_var archive failed — volume may be empty or unmounted"
    fi

    backup_end=$(date -u +%s)
    local artifacts=(mysql.sql.gz)
    [[ -f "$destination/minio-data.tar.gz" ]] && artifacts+=(minio-data.tar.gz)
    [[ -f "$destination/app-var-storage.tar.gz" ]] && artifacts+=(app-var-storage.tar.gz)

    (cd "$destination" && sha256sum "${artifacts[@]}" > SHA256SUMS)
    printf 'backup_start_utc=%s\n' "$backup_start" > "$destination/BACKUP_WINDOW"
    printf 'backup_end_utc=%s\n' "$backup_end" >> "$destination/BACKUP_WINDOW"
    printf 'storage_backend=%s\n' "$storage_backend" >> "$destination/BACKUP_WINDOW"

    printf '\nNB: This is a crash-consistent (not application-consistent) backup.\n'
    printf '   MySQL dump and object archive were taken at different instants.\n'
    printf '   For strict cross-system consistency, stop API + workers first:\n'
    printf '     docker compose --profile workers stop\n'
    printf '     docker compose stop backend-api\n'
    compose_info "backup completed: $destination"
}

compose_restore() {
    local source=${1:-}
    [[ -n "$source" ]] || compose_die "restore requires a backup directory"
    compose_require_env
    source=$(cd "$source" && pwd)
    [[ -f "$source/mysql.sql.gz" ]] || compose_die "backup directory missing mysql.sql.gz"
    [[ -f "$source/SHA256SUMS" ]] && (cd "$source" && sha256sum -c SHA256SUMS) || true

    if "${COMPOSE_CMD[@]}" ps --services --status running 2>/dev/null | grep -q .; then
        compose_die "stop the stack with 'bash scripts/docker.sh down' before restore"
    fi

    local has_minio=false has_appvar=false
    [[ -f "$source/minio-data.tar.gz" ]] && has_minio=true
    [[ -f "$source/app-var-storage.tar.gz" ]] && has_appvar=true

    compose_info "creating stopped volume helper containers"
    "${COMPOSE_CMD[@]}" create --no-deps backend-api minio >/dev/null
    local backend_container backend_image
    backend_container=$(compose_service_container_id "backend-api")
    backend_image=$(compose_service_image_id "backend-api")

    if $has_minio; then
        compose_info "restoring MinIO volume"
        local minio_container
        minio_container=$(compose_service_container_id "minio")
        $DOCKER_BIN run --rm --network none --user 0:0 \
            --volumes-from "$minio_container" --entrypoint sh "$backend_image" -c \
            'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzf - -C /data' \
            < "$source/minio-data.tar.gz"
    fi

    if $has_appvar; then
        compose_info "restoring app_var storage"
        $DOCKER_BIN run --rm --network none --user 0:0 \
            --volumes-from "$backend_container" --entrypoint sh "$backend_image" -c \
            'rm -rf /app/var/storage; tar -xzf - -C /app/var' \
            < "$source/app-var-storage.tar.gz"
    fi

    compose_info "starting MySQL for database restore"
    "${COMPOSE_CMD[@]}" up -d mysql
    "${COMPOSE_CMD[@]}" exec -T mysql sh -c \
        'until env -u MYSQL_HOST -u MYSQL_PORT mysql -S "$MYSQL_UNIX_PORT" -u root -p"$MYSQL_ROOT_PASSWORD" -Nse "SELECT 1"; do sleep 2; done'
    gzip -dc "$source/mysql.sql.gz" | "${COMPOSE_CMD[@]}" exec -T mysql sh -c \
        'exec env -u MYSQL_HOST -u MYSQL_PORT mysql -S "$MYSQL_UNIX_PORT" -u root -p"$MYSQL_ROOT_PASSWORD"'

    if [[ -f "$source/BACKUP_WINDOW" ]]; then
        compose_info "backup window markers — objects created within this window may be inconsistent"
        cat "$source/BACKUP_WINDOW"
    fi

    compose_info "restore completed; start the stack with 'bash scripts/docker.sh up'"
    compose_info "after startup, run: bash scripts/db.sh reap-storage --include-orphans (dry-run first)"
}

compose_main() {
    local command=${1:-}
    case "$command" in
        check) compose_check ;;
        build) compose_check; "${COMPOSE_CMD[@]}" build --pull ;;
        up) compose_check; "${COMPOSE_CMD[@]}" up -d --build --force-recreate --remove-orphans ;;
        up-workers) compose_up_workers ;;
        status) compose_require_env; "${COMPOSE_CMD[@]}" ps ;;
        logs) compose_require_env; "${COMPOSE_CMD[@]}" logs -f --tail=200 ;;
        smoke) compose_smoke ;;
        verify-storage) compose_verify_storage ;;
        down) compose_require_env; "${COMPOSE_CMD[@]}" --profile workers down --remove-orphans ;;
        backup) compose_backup "${2:-}" ;;
        restore) compose_restore "${2:-}" ;;
        *) compose_usage; [[ -z "$command" ]] || return 2 ;;
    esac
}
