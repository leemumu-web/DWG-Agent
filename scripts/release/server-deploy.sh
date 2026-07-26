#!/usr/bin/env bash
# Install and operate an encrypted, offline DWG-Agent server release.
set -Eeuo pipefail

server_usage() {
    cat <<'EOF'
Usage:
  server-deploy.sh install ENCRYPTED_BUNDLE TARGET_DIR
  server-deploy.sh up TARGET_DIR
  server-deploy.sh status TARGET_DIR
  server-deploy.sh smoke TARGET_DIR
  server-deploy.sh down TARGET_DIR
EOF
}

server_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
server_info() { printf '==> %s\n' "$*"; }

server_cleanup() {
    if [[ -n "${SERVER_TMP:-}" && "$SERVER_TMP" == /tmp/dwg-agent-install.* ]]; then
        find "$SERVER_TMP" -depth -delete 2>/dev/null || true
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
    docker compose --project-directory "$target" \
        -f "$target/compose.server.yaml" \
        --env-file "$target/.env.docker" "$@"
}

server_install() {
    local bundle=${1:-} target=${2:-}
    [[ -f "$bundle" ]] || server_die "encrypted bundle not found: $bundle"
    [[ -n "$target" ]] || server_die "target directory is required"
    command -v gpg >/dev/null || server_die "gpg is unavailable"
    command -v docker >/dev/null || server_die "docker is unavailable"

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

    mkdir -p "$target/infra/database/mysql" "$target/scripts"
    chmod 0750 "$target"
    install -m 0644 "$SERVER_TMP/compose.server.yaml" "$target/compose.server.yaml"
    install -m 0644 "$SERVER_TMP/infra/database/mysql/init.sql" \
        "$target/infra/database/mysql/init.sql"
    install -m 0644 "$SERVER_TMP/infra/database/mysql/hardware_handbook.sql" \
        "$target/infra/database/mysql/hardware_handbook.sql"
    install -m 0755 "$SERVER_TMP/scripts/server-deploy.sh" "$target/scripts/server-deploy.sh"
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

server_up() {
    local target=${1:-}
    server_require_target "$target"
    grep -Eq '^[A-Za-z_][A-Za-z0-9_]*=CHANGE_ME_' "$target/.env.docker" \
        && server_die "CHANGE_ME_* placeholders remain in .env.docker"
    server_compose "$target" config --quiet
    server_compose "$target" up -d --no-build --remove-orphans

    local expected_count deadline rows healthy_count
    expected_count=$(server_compose "$target" config --services | wc -l)
    [[ "$expected_count" -eq 14 ]] || server_die "server release must contain exactly 14 services"
    deadline=$((SECONDS + 240))
    while true; do
        rows=$(server_compose "$target" ps --all --format '{{.State}}|{{.Health}}')
        healthy_count=$(grep -Ec '^running\|(healthy)?$' <<<"$rows" || true)
        if [[ "$healthy_count" -eq "$expected_count" ]]; then
            server_info "all 14 services are healthy"
            break
        fi
        grep -Eq '^(exited|dead|restarting)\||^running\|unhealthy$' <<<"$rows" \
            && { server_compose "$target" ps --all; server_die "service startup failed"; }
        [[ "$SECONDS" -lt "$deadline" ]] || server_die "service startup timed out"
        sleep 2
    done
    server_smoke "$target"
}

server_status() {
    local target=${1:-}
    server_require_target "$target"
    server_compose "$target" ps --all
}

server_smoke() {
    local target=${1:-} port
    server_require_target "$target"
    port=$(awk -F= '$1 == "HTTP_PORT" {print substr($0, index($0, "=") + 1); exit}' \
        "$target/.env.docker")
    port=${port:-80}
    curl -fsS "http://127.0.0.1:${port}/nginx-health" >/dev/null
    curl -fsS "http://127.0.0.1:${port}/health/ready" >/dev/null
    server_info "gateway, MySQL and MinIO readiness passed"
    server_compose "$target" exec -T backend-api \
        python /app/scripts/release/verify_live_remnant.py \
        --fixture /app/scripts/release/fixtures/oda_runtime_smoke.dxf
    server_info "protected remnant MySQL/MinIO round-trip passed"
}

server_down() {
    local target=${1:-}
    server_require_target "$target"
    server_compose "$target" down --remove-orphans
}

case "${1:-}" in
    install) shift; server_install "$@" ;;
    up) shift; server_up "$@" ;;
    status) shift; server_status "$@" ;;
    smoke) shift; server_smoke "$@" ;;
    down) shift; server_down "$@" ;;
    -h|--help|"") server_usage ;;
    *) server_usage; server_die "unknown command: $1" ;;
esac
