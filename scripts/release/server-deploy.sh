#!/usr/bin/env bash
# Install and operate an encrypted, offline DWG-Agent server release.
set -Eeuo pipefail

SERVER_TMP=""
SERVER_ROLLBACK_TMP=""
SERVER_MAINTENANCE_LOCK_TARGET=""

server_usage() {
    cat <<'EOF'
Usage:
  server-deploy.sh install ENCRYPTED_BUNDLE TARGET_DIR
  server-deploy.sh up TARGET_DIR
  server-deploy.sh recover TARGET_DIR
  server-deploy.sh enable-service TARGET_DIR
  server-deploy.sh status TARGET_DIR
  server-deploy.sh smoke TARGET_DIR
  server-deploy.sh down TARGET_DIR
EOF
}

server_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
server_info() { printf '==> %s\n' "$*"; }

server_env_value() {
    local env_file=$1 key=$2
    [[ -f "$env_file" ]] || return 0
    awk -F= -v expected="$key" '
        $1 == expected { value = substr($0, index($0, "=") + 1) }
        END { sub(/\r$/, "", value); print value }
    ' "$env_file"
}

server_require_docker_disk_space() {
    local env_file=${1:-} minimum_gib docker_root available_kib available_gib
    minimum_gib=$(server_env_value "$env_file" DOCKER_MIN_FREE_GIB)
    minimum_gib=${minimum_gib:-20}
    [[ "$minimum_gib" =~ ^[1-9][0-9]*$ ]] \
        || server_die "DOCKER_MIN_FREE_GIB must be a positive integer"

    docker_root=$(docker info --format '{{.DockerRootDir}}') \
        || server_die "cannot determine the Docker data root"
    [[ -n "$docker_root" ]] || server_die "Docker returned an empty data root"
    available_kib=$(df -Pk -- "$docker_root" | awk 'END { print $4 }') \
        || server_die "cannot determine free space for Docker data root: $docker_root"
    [[ "$available_kib" =~ ^[0-9]+$ ]] \
        || server_die "Docker data-root free-space result is invalid"
    if (( available_kib < minimum_gib * 1024 * 1024 )); then
        available_gib=$((available_kib / 1024 / 1024))
        server_die "Docker data root $docker_root has ${available_gib} GiB free; deployment requires at least ${minimum_gib} GiB free"
    fi
    available_gib=$((available_kib / 1024 / 1024))
    server_info "Docker data root $docker_root: ${available_gib} GiB available"
}

server_cleanup() {
    if [[ -n "${SERVER_TMP:-}" && "$SERVER_TMP" == /tmp/dwg-agent-install.* ]]; then
        find "$SERVER_TMP" -depth -delete 2>/dev/null || true
    fi
    if [[ -n "${SERVER_ROLLBACK_TMP:-}" \
        && "$SERVER_ROLLBACK_TMP" == */.rollback-candidate.pending.* ]]; then
        find "$SERVER_ROLLBACK_TMP" -depth -delete 2>/dev/null || true
    fi
}

server_require_target() {
    local target=$1
    [[ -f "$target/compose.server.yaml" ]] || server_die "server release is not installed: $target"
    [[ -f "$target/.env.docker" ]] || server_die "missing $target/.env.docker"
}

server_compose() {
    local target=$1
    shift
    docker compose --project-name dwg-agent --project-directory "$target" \
        -f "$target/compose.server.yaml" \
        --env-file "$target/.env.docker" "$@"
}

server_acquire_maintenance_lock() {
    local target=$1 resolved
    [[ -d "$target" ]] || return 0
    resolved=$(realpath -e -- "$target")
    if [[ -n "$SERVER_MAINTENANCE_LOCK_TARGET" ]]; then
        [[ "$SERVER_MAINTENANCE_LOCK_TARGET" == "$resolved" ]] \
            || server_die "a different server target is already locked by this process"
        return 0
    fi
    command -v flock >/dev/null || server_die "flock is unavailable"
    exec 8>"$resolved/.timezone-migration.lock"
    flock -n 8 || server_die "timezone maintenance is running; refusing concurrent server mutation"
    SERVER_MAINTENANCE_LOCK_TARGET=$resolved
}

server_release_maintenance_lock() {
    [[ -n "$SERVER_MAINTENANCE_LOCK_TARGET" ]] || return 0
    flock -u 8
    exec 8>&-
    SERVER_MAINTENANCE_LOCK_TARGET=""
}

server_preserve_rollback_candidate() {
    local target=$1 rollback_candidate="$1/.rollback-candidate"
    [[ -f "$target/.env.docker" && -f "$target/RELEASE" \
        && -f "$target/images.manifest" ]] \
        || server_die "installed release is incomplete; refusing to overwrite rollback evidence"
    if [[ -e "$rollback_candidate" ]]; then
        [[ ! -L "$rollback_candidate" && -d "$rollback_candidate" \
            && -f "$rollback_candidate/compose.server.yaml" \
            && -f "$rollback_candidate/.env.docker" \
            && -f "$rollback_candidate/RELEASE" \
            && -f "$rollback_candidate/images.manifest" ]] \
            || server_die "existing rollback candidate is unsafe or incomplete"
        server_info "kept the existing immutable rollback candidate"
        return 0
    fi
    SERVER_ROLLBACK_TMP=$(mktemp -d "$target/.rollback-candidate.pending.XXXXXX")
    chmod 0700 "$SERVER_ROLLBACK_TMP"
    install -m 0644 "$target/compose.server.yaml" \
        "$SERVER_ROLLBACK_TMP/compose.server.yaml"
    install -m 0600 "$target/.env.docker" "$SERVER_ROLLBACK_TMP/.env.docker"
    install -m 0644 "$target/RELEASE" "$SERVER_ROLLBACK_TMP/RELEASE"
    install -m 0644 "$target/images.manifest" "$SERVER_ROLLBACK_TMP/images.manifest"
    mv -T -- "$SERVER_ROLLBACK_TMP" "$rollback_candidate"
    SERVER_ROLLBACK_TMP=""
    server_info "preserved the currently installed release as an immutable rollback candidate"
}

server_install() {
    local bundle=${1:-} target=${2:-}
    [[ -f "$bundle" ]] || server_die "encrypted bundle not found: $bundle"
    [[ -n "$target" ]] || server_die "target directory is required"
    server_acquire_maintenance_lock "$target"
    command -v gpg >/dev/null || server_die "gpg is unavailable"
    command -v docker >/dev/null || server_die "docker is unavailable"
    server_require_docker_disk_space "$target/.env.docker"

    local checksum_file="${bundle}.sha256"
    [[ -f "$checksum_file" ]] || server_die "outer checksum is missing: $checksum_file"
    (cd "$(dirname "$bundle")" && sha256sum -c "$(basename "$checksum_file")")
    if [[ -f "${bundle}.asc" ]]; then
        gpg --batch --verify "${bundle}.asc" "$bundle"
    fi

    SERVER_TMP=$(mktemp -d /tmp/dwg-agent-install.XXXXXX)
    trap server_cleanup EXIT
    gpg --batch --decrypt "$bundle" | gzip -dc | tar -xf - -C "$SERVER_TMP"
    (
        cd "$SERVER_TMP"
        sha256sum -c SHA256SUMS
    )
    docker image load -i "$SERVER_TMP/images.tar"

    local image_ref expected_id actual_id
    while IFS=$'\t' read -r image_ref expected_id; do
        [[ -n "$image_ref" && -n "$expected_id" ]] || server_die "invalid image manifest row"
        actual_id=$(docker image inspect "$image_ref" --format '{{.Id}}')
        [[ "$actual_id" == "$expected_id" ]] \
            || server_die "loaded image ID mismatch: $image_ref"
    done < "$SERVER_TMP/images.manifest"

    if [[ -f "$target/compose.server.yaml" ]]; then
        server_preserve_rollback_candidate "$target"
    fi

    mkdir -p "$target/infra/database/mysql" "$target/scripts"
    chmod 0750 "$target"
    install -m 0644 "$SERVER_TMP/compose.server.yaml" "$target/compose.server.yaml"
    install -m 0644 "$SERVER_TMP/infra/database/mysql/init.sql" \
        "$target/infra/database/mysql/init.sql"
    install -m 0644 "$SERVER_TMP/infra/database/mysql/hardware_handbook.sql" \
        "$target/infra/database/mysql/hardware_handbook.sql"
    install -m 0755 "$SERVER_TMP/scripts/server-deploy.sh" "$target/scripts/server-deploy.sh"
    install -m 0755 "$SERVER_TMP/scripts/server-timezone-migrate.sh" \
        "$target/scripts/server-timezone-migrate.sh"
    install -m 0644 "$SERVER_TMP/RELEASE" "$target/RELEASE"
    install -m 0644 "$SERVER_TMP/images.manifest" "$target/images.manifest"
    if [[ ! -f "$target/.env.docker" ]]; then
        install -m 0600 "$SERVER_TMP/.env.docker.example" "$target/.env.docker"
        server_info "installed; edit $target/.env.docker and replace every CHANGE_ME_* value"
        return 0
    fi
    chmod 0600 "$target/.env.docker"
    server_info "installed; existing runtime secrets were preserved"
}

server_wait_services() {
    local target=$1 timeout=$2
    shift 2
    local -a services=("$@")
    [[ "${#services[@]}" -gt 0 ]] || server_die "no services supplied to readiness gate"

    local deadline rows service line state health ready_count
    deadline=$((SECONDS + timeout))
    while true; do
        rows=$(server_compose "$target" ps --all \
            --format '{{.Service}}|{{.State}}|{{.Health}}' "${services[@]}")
        ready_count=0
        for service in "${services[@]}"; do
            line=$(awk -F'|' -v expected="$service" '$1 == expected {print; exit}' <<<"$rows")
            if [[ -z "$line" ]]; then
                continue
            fi
            IFS='|' read -r _ state health <<<"$line"
            if [[ "$state" == "running" && ( -z "$health" || "$health" == "healthy" ) ]]; then
                ready_count=$((ready_count + 1))
            fi
        done
        if [[ "$ready_count" -eq "${#services[@]}" ]]; then
            server_info "${services[*]} are healthy"
            return 0
        fi
        if [[ "$SECONDS" -ge "$deadline" ]]; then
            server_compose "$target" ps --all "${services[@]}" >&2 || true
            server_die "service readiness timed out: ${services[*]}"
        fi
        sleep 2
    done
}

server_wait_all_services() {
    local target=$1 timeout=$2
    local -a services
    mapfile -t services < <(server_compose "$target" config --services)
    [[ "${#services[@]}" -eq 17 ]] || server_die "server release must contain exactly 17 services"
    server_wait_services "$target" "$timeout" "${services[@]}"
}

server_validate_runtime() {
    local target=$1
    server_require_target "$target"
    server_require_docker_disk_space "$target/.env.docker"
    grep -Eq '^[A-Za-z_][A-Za-z0-9_]*=CHANGE_ME_' "$target/.env.docker" \
        && server_die "CHANGE_ME_* placeholders remain in .env.docker"
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
        actual=$(awk -F= -v expected="$key" '
            $1 == expected { value = substr($0, index($0, "=") + 1) }
            END { sub(/\r$/, "", value); print value }
        ' "$target/.env.docker")
        actual=${actual,,}
        [[ "$actual" == "$wanted" ]] \
            || server_die "$key must be $wanted in the production .env.docker"
    done
    server_compose "$target" config --quiet
}

server_recover() {
    local target=${1:-}
    server_acquire_maintenance_lock "$target"
    server_validate_runtime "$target"

    server_compose "$target" up -d --no-build mysql minio
    server_wait_services "$target" 240 mysql minio

    server_compose "$target" up -d --no-build backend-api
    server_wait_services "$target" 240 backend-api

    server_compose "$target" up -d --no-build --remove-orphans
    server_wait_all_services "$target" 360
    server_smoke "$target"
}

server_up() {
    server_recover "${1:-}"
}

server_enable_service() {
    local target=${1:-}
    [[ "$EUID" -eq 0 ]] || server_die "enable-service must run as root"
    command -v systemctl >/dev/null || server_die "systemctl is unavailable"
    target=$(realpath -e "$target")
    [[ "$target" =~ ^/[A-Za-z0-9._/-]+$ ]] \
        || server_die "TARGET_DIR contains unsupported systemd path characters"
    server_acquire_maintenance_lock "$target"
    server_validate_runtime "$target"

    local unit_tmp
    unit_tmp=$(mktemp /tmp/dwg-agent-systemd.XXXXXX)
    cat >"$unit_tmp" <<EOF
[Unit]
Description=DWG Agent production stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$target
ExecStart=$target/scripts/server-deploy.sh recover $target
ExecReload=$target/scripts/server-deploy.sh recover $target
ExecStop=$target/scripts/server-deploy.sh down $target
TimeoutStartSec=900
TimeoutStopSec=240
Restart=on-failure
RestartSec=15s

[Install]
WantedBy=multi-user.target
EOF
    install -m 0644 "$unit_tmp" /etc/systemd/system/dwg-agent.service
    rm -f "$unit_tmp"
    systemctl daemon-reload
    systemctl enable dwg-agent.service
    server_release_maintenance_lock
    systemctl start dwg-agent.service
    server_info "dwg-agent.service is enabled and active"
}

server_status() {
    local target=${1:-}
    server_require_target "$target"
    server_compose "$target" ps --all
}

server_smoke() {
    local target=${1:-} port
    server_acquire_maintenance_lock "$target"
    server_require_target "$target"
    port=$(awk -F= '$1 == "HTTP_PORT" {print substr($0, index($0, "=") + 1); exit}' \
        "$target/.env.docker")
    port=${port:-80}
    curl -fsS "http://127.0.0.1:${port}/nginx-health" >/dev/null
    curl -fsS "http://127.0.0.1:${port}/health/ready" >/dev/null
    server_info "gateway, MySQL and MinIO readiness passed"
    server_compose "$target" exec -T backend-api \
        python /app/scripts/release/verify_runtime_features.py
    server_info "production runtime feature matrix passed"
    server_compose "$target" exec -T backend-api \
        python /app/scripts/release/verify_live_remnant.py \
        --fixture /app/scripts/release/fixtures/oda_runtime_smoke.dxf
    server_info "protected remnant MySQL/MinIO round-trip passed"
}

server_down() {
    local target=${1:-}
    server_acquire_maintenance_lock "$target"
    server_require_target "$target"
    server_compose "$target" down --remove-orphans
}

case "${1:-}" in
    install) shift; server_install "$@" ;;
    up) shift; server_up "$@" ;;
    recover) shift; server_recover "$@" ;;
    enable-service) shift; server_enable_service "$@" ;;
    status) shift; server_status "$@" ;;
    smoke) shift; server_smoke "$@" ;;
    down) shift; server_down "$@" ;;
    -h|--help|"") server_usage ;;
    *) server_usage; server_die "unknown command: $1" ;;
esac
