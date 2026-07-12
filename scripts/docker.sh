#!/usr/bin/env bash
# DWG-Agent Docker deployment helper. It never prints secret values.
set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env.docker"
COMPOSE=(docker compose --project-directory "$PROJECT_ROOT" --env-file "$ENV_FILE")

usage() {
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

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }

require_env() {
    [[ -f "$ENV_FILE" ]] || die "missing .env.docker; copy .env.docker.example and replace placeholders"
    if grep -Eq '^[A-Za-z_][A-Za-z0-9_]*=CHANGE_ME_' "$ENV_FILE"; then
        die ".env.docker still contains CHANGE_ME_* placeholders"
    fi
    local required=(MYSQL_PASSWORD MYSQL_ROOT_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD JWT_SECRET_KEY SUPER_ADMIN_PASSWORD)
    local key
    for key in "${required[@]}"; do
        grep -Eq "^${key}=.+" "$ENV_FILE" || die "$key is missing or empty in .env.docker"
    done
}

check_source() {
    [[ -f "$PROJECT_ROOT/Stages/dxf2excel/pyproject.toml" ]] || die "Stages/dxf2excel source is absent"
    if git -C "$PROJECT_ROOT" ls-files -s Stages/dxf2excel | grep -q '^160000 '; then
        printf 'WARNING: Stages/dxf2excel is an unreproducible gitlink; this checkout can build, but a clean clone cannot.\n' >&2
    fi
}

check() {
    command -v docker >/dev/null || die "docker is not installed"
    docker info >/dev/null || die "Docker daemon is unavailable"
    require_env
    check_source
    "${COMPOSE[@]}" config --quiet
    "${COMPOSE[@]}" --profile workers config --quiet
    info "Docker deployment checks passed"
}

public_port() {
    local value
    value=$(grep -E '^HTTP_PORT=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)
    printf '%s' "${value:-80}"
}

backup() {
    local destination=${1:-}
    [[ -n "$destination" ]] || die "backup requires a destination directory"
    require_env
    mkdir -p "$destination"
    destination=$(cd "$destination" && pwd)

    local storage_backend
    storage_backend=$(grep -E '^STORAGE_BACKEND=' "$ENV_FILE" | tail -n1 | cut -d= -f2- || echo "minio")

    # Record backup window for post-restore reconciliation.
    local backup_start backup_end
    backup_start=$(date -u +%s)

    info "creating consistent MySQL dump"
    "${COMPOSE[@]}" exec -T mysql sh -c \
        'exec mysqldump -u root -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --events --triggers --databases "$MYSQL_DATABASE" hardware_handbook' \
        | gzip -9 > "$destination/mysql.sql.gz"

    if [[ "$storage_backend" == "minio" ]]; then
        info "archiving MinIO object data"
        "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh minio -c \
            'cd /data && tar -czf - .' > "$destination/minio-data.tar.gz"
    else
        warn "STORAGE_BACKEND=$storage_backend — MinIO archive skipped; objects in app_var volume"
        info "archiving app_var/storage (local backend)"
        "${COMPOSE[@]}" run --rm --no-deps -T -v app_var:/appvar --entrypoint sh minio -c \
            'cd /appvar && tar -czf - storage' > "$destination/app-var-storage.tar.gz" 2>/dev/null || \
            warn "app_var archive failed — volume may be empty or unmounted"
    fi

    backup_end=$(date -u +%s)

    # Write backup manifest
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
    info "backup completed: $destination"
}

restore() {
    local source=${1:-}
    [[ -n "$source" ]] || die "restore requires a backup directory"
    require_env
    source=$(cd "$source" && pwd)
    [[ -f "$source/mysql.sql.gz" ]] || die "backup directory missing mysql.sql.gz"
    [[ -f "$source/SHA256SUMS" ]] && (cd "$source" && sha256sum -c SHA256SUMS) || true

    if "${COMPOSE[@]}" ps --services --status running 2>/dev/null | grep -q .; then
        die "stop the stack with 'bash scripts/docker.sh down' before restore"
    fi

    local has_minio=false has_appvar=false
    [[ -f "$source/minio-data.tar.gz" ]] && has_minio=true
    [[ -f "$source/app-var-storage.tar.gz" ]] && has_appvar=true

    if $has_minio; then
        info "restoring MinIO volume"
        "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh minio -c \
            'find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar -xzf - -C /data' \
            < "$source/minio-data.tar.gz"
    fi

    if $has_appvar; then
        info "restoring app_var storage"
        "${COMPOSE[@]}" run --rm --no-deps -T -v app_var:/appvar --entrypoint sh minio -c \
            'rm -rf /appvar/storage; tar -xzf - -C /appvar' \
            < "$source/app-var-storage.tar.gz"
    fi

    info "starting MySQL for database restore"
    "${COMPOSE[@]}" up -d mysql
    "${COMPOSE[@]}" exec -T mysql sh -c \
        'until mysqladmin ping -h 127.0.0.1 -u root -p"$MYSQL_ROOT_PASSWORD" --silent; do sleep 2; done'
    gzip -dc "$source/mysql.sql.gz" | "${COMPOSE[@]}" exec -T mysql sh -c \
        'exec mysql -u root -p"$MYSQL_ROOT_PASSWORD"'

    # Post-restore: check for files whose storage_key may not exist in the
    # restored volume (objects uploaded during the backup window, see BACKUP_WINDOW).
    if [[ -f "$source/BACKUP_WINDOW" ]]; then
        info "backup window markers — objects created within this window may be inconsistent"
        cat "$source/BACKUP_WINDOW"
    fi

    info "restore completed; start the stack with 'bash scripts/docker.sh up'"
    info "after startup, run: bash scripts/db.sh reap-storage --include-orphans (dry-run first)"
}

command=${1:-}
case "$command" in
    check) check ;;
    build) check; "${COMPOSE[@]}" build --pull ;;
    up) check; "${COMPOSE[@]}" up -d --build --remove-orphans ;;
    up-workers) check; "${COMPOSE[@]}" --profile workers up -d --build --remove-orphans ;;
    status) require_env; "${COMPOSE[@]}" ps ;;
    logs) require_env; "${COMPOSE[@]}" logs -f --tail=200 ;;
    smoke)
        require_env
        port=$(public_port)
        curl -fsS "http://127.0.0.1:${port}/nginx-health" >/dev/null
        curl -fsS "http://127.0.0.1:${port}/health/ready" >/dev/null
        info "public gateway and backend readiness checks passed"
        ;;
    down) require_env; "${COMPOSE[@]}" down --remove-orphans ;;
    backup) backup "${2:-}" ;;
    restore) restore "${2:-}" ;;
    *) usage; [[ -z "$command" ]] || exit 2 ;;
esac
