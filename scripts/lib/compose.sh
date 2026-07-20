#!/usr/bin/env bash
# Docker Compose deployment, backup and restore implementation.

if [ "${DWG_COMPOSE_LIB_LOADED:-0}" = "1" ]; then
    return 0
fi
DWG_COMPOSE_LIB_LOADED=1

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

DOCKER_ENV_FILE="$PROJECT_ROOT/.env.docker"
COMPOSE_CMD=(docker compose --project-directory "$PROJECT_ROOT" --env-file "$DOCKER_ENV_FILE")

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
  down        Stop containers while preserving data volumes
  backup DIR  Back up MySQL and MinIO into DIR
  restore DIR Restore a stopped stack from a backup created by this script
EOF
}

compose_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
compose_info() { printf '==> %s\n' "$*"; }
compose_warn() { printf 'WARNING: %s\n' "$*" >&2; }

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
}

compose_check_source() {
    [[ -f "$PROJECT_ROOT/Stages/dxf2excel/pyproject.toml" ]] || compose_die "Stages/dxf2excel source is absent"
    if git -C "$PROJECT_ROOT" ls-files -s Stages/dxf2excel | grep -q '^160000 '; then
        compose_warn "Stages/dxf2excel is an unreproducible gitlink; this checkout can build, but a clean clone cannot."
    fi
}

compose_check() {
    command -v docker >/dev/null || compose_die "docker is not installed"
    docker info >/dev/null || compose_die "Docker daemon is unavailable"
    compose_require_env
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

compose_backup() {
    local destination=${1:-}
    [[ -n "$destination" ]] || compose_die "backup requires a destination directory"
    compose_require_env
    mkdir -p "$destination"
    destination=$(cd "$destination" && pwd)

    local storage_backend
    storage_backend=$(grep -E '^STORAGE_BACKEND=' "$DOCKER_ENV_FILE" | tail -n1 | cut -d= -f2- || echo "minio")

    local backup_start backup_end
    backup_start=$(date -u +%s)

    compose_info "creating consistent MySQL dump"
    "${COMPOSE_CMD[@]}" exec -T mysql sh -c \
        'exec mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --events --triggers --databases "$MYSQL_DATABASE" hardware_handbook' \
        | gzip -9 > "$destination/mysql.sql.gz"

    if [[ "$storage_backend" == "minio" ]]; then
        compose_info "archiving MinIO object data"
        "${COMPOSE_CMD[@]}" run --rm --no-deps -T --entrypoint sh minio -c \
            'cd /data && tar -czf - .' > "$destination/minio-data.tar.gz"
    else
        compose_warn "STORAGE_BACKEND=$storage_backend — MinIO archive skipped; objects in app_var volume"
        compose_info "archiving app_var/storage (local backend)"
        "${COMPOSE_CMD[@]}" run --rm --no-deps -T -v app_var:/appvar --entrypoint sh minio -c \
            'cd /appvar && tar -czf - storage' > "$destination/app-var-storage.tar.gz" 2>/dev/null || \
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

    if $has_minio; then
        compose_info "restoring MinIO volume"
        "${COMPOSE_CMD[@]}" run --rm --no-deps -T --entrypoint sh minio -c \
            'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzf - -C /data' \
            < "$source/minio-data.tar.gz"
    fi

    if $has_appvar; then
        compose_info "restoring app_var storage"
        "${COMPOSE_CMD[@]}" run --rm --no-deps -T -v app_var:/appvar --entrypoint sh minio -c \
            'rm -rf /appvar/storage; tar -xzf - -C /appvar' \
            < "$source/app-var-storage.tar.gz"
    fi

    compose_info "starting MySQL for database restore"
    "${COMPOSE_CMD[@]}" up -d mysql
    "${COMPOSE_CMD[@]}" exec -T mysql sh -c \
        'until mysqladmin ping -h 127.0.0.1 -u root -p"$MYSQL_ROOT_PASSWORD" --silent; do sleep 2; done'
    gzip -dc "$source/mysql.sql.gz" | "${COMPOSE_CMD[@]}" exec -T mysql sh -c \
        'exec mysql -u root -p"$MYSQL_ROOT_PASSWORD"'

    if [[ -f "$source/BACKUP_WINDOW" ]]; then
        compose_info "backup window markers — objects created within this window may be inconsistent"
        cat "$source/BACKUP_WINDOW"
    fi

    compose_info "restore completed; start the stack with 'bash scripts/docker.sh up'"
    compose_info "after startup, run: bash scripts/db.sh reap-storage --include-orphans (dry-run first)"
}

compose_main() {
    local command=${1:-} port
    case "$command" in
        check) compose_check ;;
        build) compose_check; "${COMPOSE_CMD[@]}" build --pull ;;
        up) compose_check; "${COMPOSE_CMD[@]}" up -d --build --remove-orphans ;;
        up-workers) compose_check; "${COMPOSE_CMD[@]}" --profile workers up -d --build --remove-orphans ;;
        status) compose_require_env; "${COMPOSE_CMD[@]}" ps ;;
        logs) compose_require_env; "${COMPOSE_CMD[@]}" logs -f --tail=200 ;;
        smoke)
            compose_require_env
            port=$(compose_public_port)
            curl -fsS "http://127.0.0.1:${port}/nginx-health" >/dev/null
            curl -fsS "http://127.0.0.1:${port}/health/ready" >/dev/null
            compose_info "public gateway and backend readiness checks passed"
            ;;
        down) compose_require_env; "${COMPOSE_CMD[@]}" down --remove-orphans ;;
        backup) compose_backup "${2:-}" ;;
        restore) compose_restore "${2:-}" ;;
        *) compose_usage; [[ -z "$command" ]] || return 2 ;;
    esac
}
