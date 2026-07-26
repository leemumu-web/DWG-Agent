#!/usr/bin/env bash
# Build and encrypt a complete offline server release. No runtime secret is copied.
set -Eeuo pipefail

source "$(dirname "$0")/lib/common.sh"

release_usage() {
    cat <<'EOF'
Usage:
  bash scripts/release.sh bundle --recipient GPG_RECIPIENT --output DIR [options]

Options:
  --version NAME       Immutable release tag (default: UTC timestamp + git short SHA)
  --signing-key KEY    Also create an armored detached signature
  --skip-build         Package the currently tagged, already verified local images
EOF
}

release_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
release_info() { printf '==> %s\n' "$*"; }

release_cleanup() {
    if [[ -n "${RELEASE_TMP:-}" && "$RELEASE_TMP" == /tmp/dwg-agent-release.* ]]; then
        find "$RELEASE_TMP" -depth -delete 2>/dev/null || true
    fi
}

release_env_value() {
    env_value "$PROJECT_ROOT/.env.docker" "$1"
}

release_verify_protected_image() {
    local image=$1
    docker run --rm --network none --read-only --entrypoint sh "$image" -c '
        set -eu
        source_count=$(find /app/app /app/Stages -type f -name "*.py" | wc -l)
        if [ "$source_count" -ne 0 ]; then
            echo "business Python source remains in protected image" >&2
            exit 41
        fi
        sample_count=$(find /app/Stages -type f \( -name "*.xls" -o -name "*.xlsx" \) | wc -l)
        if [ "$sample_count" -ne 0 ]; then
            echo "Excel samples remain in protected image" >&2
            exit 42
        fi
        test_count=$(find /app/Stages -type d \( -name tests -o -name test \) | wc -l)
        if [ "$test_count" -ne 0 ]; then
            echo "Stage tests remain in protected image" >&2
            exit 43
        fi
        test "$(id -u)" = "1000"
        python -c "import app.main, steel_dxf_classifier, steel_dxf_split, dxf2excel"
        test "$(alembic heads | wc -l)" = "1"
    '
}

release_bundle() {
    local recipient="" output_dir="" version="" signing_key="" skip_build=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --recipient) recipient=${2:-}; shift 2 ;;
            --output) output_dir=${2:-}; shift 2 ;;
            --version) version=${2:-}; shift 2 ;;
            --signing-key) signing_key=${2:-}; shift 2 ;;
            --skip-build) skip_build=true; shift ;;
            *) release_die "unknown bundle option: $1" ;;
        esac
    done

    [[ -n "$recipient" ]] || release_die "--recipient is required; plaintext releases are forbidden"
    [[ -n "$output_dir" ]] || release_die "--output is required"
    [[ -f "$PROJECT_ROOT/.env.docker" ]] || release_die ".env.docker is required for image resolution"
    command -v docker >/dev/null || release_die "docker is unavailable"
    command -v gpg >/dev/null || release_die "gpg is unavailable"
    command -v gzip >/dev/null || release_die "gzip is unavailable"

    if [[ -z "$version" ]]; then
        local commit_id release_time
        commit_id=$(git -C "$PROJECT_ROOT" rev-parse --short=12 HEAD)
        release_time=$(date -u +%Y%m%dT%H%M%SZ)
        version="${release_time}-${commit_id}"
    fi
    [[ "$version" =~ ^[A-Za-z0-9._-]+$ ]] || release_die "version contains unsafe characters"

    mkdir -p "$output_dir"
    output_dir=$(cd "$output_dir" && pwd)
    local bundle="$output_dir/dwg-agent-${version}.tar.gz.gpg"
    local installer="$output_dir/dwg-agent-${version}-deploy.sh"
    [[ ! -e "$bundle" ]] || release_die "release already exists: $bundle"
    [[ ! -e "$installer" ]] || release_die "release installer already exists: $installer"

    if ! $skip_build; then
        bash "$PROJECT_ROOT/scripts/docker.sh" check
        docker compose --project-directory "$PROJECT_ROOT" \
            --env-file "$PROJECT_ROOT/.env.docker" --profile workers build
    fi

    local backend_source frontend_source backend_release frontend_release
    backend_source=$(release_env_value DWG_AGENT_IMAGE)
    frontend_source=$(release_env_value DWG_AGENT_FRONTEND_IMAGE)
    backend_source=${backend_source:-dwg-agent-backend:local}
    frontend_source=${frontend_source:-dwg-agent-frontend:local}
    backend_release="dwg-agent-backend:${version}"
    frontend_release="dwg-agent-frontend:${version}"
    docker image inspect "$backend_source" >/dev/null
    docker image inspect "$frontend_source" >/dev/null
    docker image tag "$backend_source" "$backend_release"
    docker image tag "$frontend_source" "$frontend_release"
    release_verify_protected_image "$backend_release"

    RELEASE_TMP=$(mktemp -d /tmp/dwg-agent-release.XXXXXX)
    trap release_cleanup EXIT
    local payload="$RELEASE_TMP/payload"
    mkdir -p "$payload/infra/database/mysql" "$payload/scripts"

    "$PROJECT_ROOT/backend/.venv/bin/python" \
        "$PROJECT_ROOT/scripts/release/render_server_compose.py" \
        --source "$PROJECT_ROOT/compose.yaml" \
        --output "$payload/compose.server.yaml" \
        --backend-image "$backend_release" \
        --frontend-image "$frontend_release"
    install -m 0644 "$PROJECT_ROOT/.env.docker.example" "$payload/.env.docker.example"
    install -m 0644 "$PROJECT_ROOT/infra/database/mysql/init.sql" \
        "$payload/infra/database/mysql/init.sql"
    install -m 0644 "$PROJECT_ROOT/infra/database/mysql/hardware_handbook.sql" \
        "$payload/infra/database/mysql/hardware_handbook.sql"
    install -m 0755 "$PROJECT_ROOT/scripts/release/server-deploy.sh" \
        "$payload/scripts/server-deploy.sh"

    local -a compose_cmd=(docker compose --project-directory "$PROJECT_ROOT" \
        --env-file "$PROJECT_ROOT/.env.docker")
    local -a configured_images images
    mapfile -t configured_images < <("${compose_cmd[@]}" --profile workers config --images | sort -u)
    images=("$backend_release" "$frontend_release")
    local configured
    for configured in "${configured_images[@]}"; do
        case "$configured" in
            "$backend_source"|"$frontend_source") ;;
            *) images+=("$configured") ;;
        esac
    done

    : > "$payload/images.manifest"
    local image_ref image_id
    for image_ref in "${images[@]}"; do
        image_id=$(docker image inspect "$image_ref" --format '{{.Id}}')
        printf '%s\t%s\n' "$image_ref" "$image_id" >> "$payload/images.manifest"
    done
    docker image save -o "$payload/images.tar" "${images[@]}"
    "$PROJECT_ROOT/backend/.venv/bin/python" \
        "$PROJECT_ROOT/scripts/release/verify_image_archive.py" \
        --archive "$payload/images.tar" \
        --image "$backend_release"
    printf 'release_version=%s\ncreated_utc=%s\n' \
        "$version" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$payload/RELEASE"
    (
        cd "$payload"
        find . -type f ! -name SHA256SUMS -print0 \
            | sort -z \
            | xargs -0 sha256sum > SHA256SUMS
    )

    tar -C "$payload" -cf - . \
        | gzip -9 \
        | gpg --batch --yes --encrypt --trust-model always \
            --recipient "$recipient" --output "$bundle"
    install -m 0755 "$PROJECT_ROOT/scripts/release/server-deploy.sh" "$installer"
    (
        cd "$output_dir"
        sha256sum "$(basename "$bundle")" "$(basename "$installer")" \
            > "$(basename "$bundle").sha256"
    )
    if [[ -n "$signing_key" ]]; then
        gpg --batch --yes --local-user "$signing_key" \
            --detach-sign --armor --output "$bundle.asc" "$bundle"
    fi
    release_info "encrypted server release created: $bundle"
    release_info "standalone installer created: $installer"
    release_info "no runtime .env.docker or repository source was included"
}

case "${1:-}" in
    bundle) shift; release_bundle "$@" ;;
    -h|--help|"") release_usage ;;
    *) release_usage; release_die "unknown command: $1" ;;
esac
