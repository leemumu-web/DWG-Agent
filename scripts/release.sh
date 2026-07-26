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
    docker run --rm --network none --read-only \
        --tmpfs /tmp:rw,noexec,nosuid,size=256m \
        --tmpfs /home/appuser:rw,nosuid,size=128m,uid=1000,gid=1000,mode=0700 \
        --entrypoint sh "$image" -c '
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
        python -c "
import app.main
import importlib
import sys
import dxf2excel
import dwg_converter, dxf_converter
import remnant_drawing_reader
import steel_dxf_classifier, steel_dxf_split
from app.modules.excel_processing.stage_adapter import get_excel_final_stage_root
from dwg_converter.check_env import check_environment as check_dwg_environment
from dxf_converter.check_env import check_environment as check_dxf_environment
from steel_dxf_split.box.release import load_verified_box_release_attestation
excel_root = get_excel_final_stage_root()
sys.path.insert(0, str(excel_root))
for module_name in (\"config\", \"handbook\", \"material_routing\", \"pipeline\", \"main\"):
    importlib.import_module(module_name)
assert check_dwg_environment().ok
assert check_dxf_environment().ok
load_verified_box_release_attestation()
"
        test "$(alembic heads | wc -l)" = "1"
        python -m dxf2excel --help | grep -q "extract"
        steel-dxf-classify --version | grep -q "steel-dxf-classifier"
        steel-dxf-split --help >/dev/null
        remnant-drawing-read --help >/dev/null
    '
}

release_verify_oda_roundtrip() {
    local image=$1
    local sample="$PROJECT_ROOT/scripts/release/fixtures/oda_runtime_smoke.dxf"
    [[ -f "$sample" ]] || release_die "ODA release smoke sample is missing: $sample"

    # Match the hardened production workers: /tmp stays noexec while the
    # AppImage extracts onto the executable /app/var volume selected by TMPDIR.
    # The anonymous volume is removed automatically with the container.
    docker run --rm --network none --read-only \
        --tmpfs /tmp:rw,noexec,nosuid,size=256m \
        --tmpfs /work:rw,nosuid,size=64m,uid=1000,gid=1000,mode=0755 \
        --tmpfs /home/appuser:rw,nosuid,size=128m,uid=1000,gid=1000,mode=0700 \
        --mount type=volume,destination=/app/var \
        --mount "type=bind,source=$sample,destination=/input/source.dxf,readonly" \
        --env TMPDIR=/app/var/appimage-tmp \
        --entrypoint python "$image" -c '
import os
from pathlib import Path

from dxf_converter.service import convert_file as convert_dxf_to_dwg
from dwg_converter.service import convert_file as convert_dwg_to_dxf

Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)
source = Path("/input/source.dxf")
dwg_result = convert_dxf_to_dwg(
    source,
    Path("/work/dwg"),
    version="ACAD2018",
    audit=True,
    timeout=120,
    retries=0,
)
if not dwg_result.success or not dwg_result.target.is_file():
    raise SystemExit(f"APPIMAGE runtime failed DXF to DWG: {dwg_result.error}")
if dwg_result.target.stat().st_size < 1024:
    raise SystemExit("APPIMAGE runtime produced an empty DWG")

dxf_result = convert_dwg_to_dxf(
    dwg_result.target,
    Path("/work/dxf"),
    version="ACAD2018",
    audit=True,
    timeout=120,
    retries=0,
)
if not dxf_result.success or not dxf_result.target.is_file():
    raise SystemExit(f"APPIMAGE runtime failed DWG to DXF: {dxf_result.error}")
if dxf_result.target.stat().st_size < 1024:
    raise SystemExit("APPIMAGE runtime produced an empty DXF")
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
            --env-file "$PROJECT_ROOT/.env.docker" build backend-api nginx
    fi

    local backend_source frontend_source mysql_source minio_source
    local backend_release frontend_release mysql_release minio_release
    backend_source=$(release_env_value DWG_AGENT_IMAGE)
    frontend_source=$(release_env_value DWG_AGENT_FRONTEND_IMAGE)
    backend_source=${backend_source:-dwg-agent-backend:local}
    frontend_source=${frontend_source:-dwg-agent-frontend:local}
    local -a compose_cmd=(docker compose --project-directory "$PROJECT_ROOT" \
        --env-file "$PROJECT_ROOT/.env.docker")
    mysql_source=$("${compose_cmd[@]}" config --format json \
        | "$PROJECT_ROOT/backend/.venv/bin/python" -c \
            'import json, sys; print(json.load(sys.stdin)["services"]["mysql"]["image"])')
    minio_source=$("${compose_cmd[@]}" config --format json \
        | "$PROJECT_ROOT/backend/.venv/bin/python" -c \
            'import json, sys; print(json.load(sys.stdin)["services"]["minio"]["image"])')
    backend_release="dwg-agent-backend:${version}"
    frontend_release="dwg-agent-frontend:${version}"
    mysql_release="dwg-agent-mysql:${version}"
    minio_release="dwg-agent-minio:${version}"
    docker image inspect "$backend_source" >/dev/null
    docker image inspect "$frontend_source" >/dev/null
    docker image inspect "$mysql_source" >/dev/null
    docker image inspect "$minio_source" >/dev/null
    docker image tag "$backend_source" "$backend_release"
    docker image tag "$frontend_source" "$frontend_release"
    docker image tag "$mysql_source" "$mysql_release"
    docker image tag "$minio_source" "$minio_release"
    release_verify_protected_image "$backend_release"
    release_verify_oda_roundtrip "$backend_release"

    RELEASE_TMP=$(mktemp -d /tmp/dwg-agent-release.XXXXXX)
    trap release_cleanup EXIT
    local payload="$RELEASE_TMP/payload"
    mkdir -p "$payload/infra/database/mysql" "$payload/scripts"

    "$PROJECT_ROOT/backend/.venv/bin/python" \
        "$PROJECT_ROOT/scripts/release/render_server_compose.py" \
        --source "$PROJECT_ROOT/compose.yaml" \
        --output "$payload/compose.server.yaml" \
        --backend-image "$backend_release" \
        --frontend-image "$frontend_release" \
        --mysql-image "$mysql_release" \
        --minio-image "$minio_release"
    install -m 0644 "$PROJECT_ROOT/.env.docker.example" "$payload/.env.docker.example"
    install -m 0644 "$PROJECT_ROOT/infra/database/mysql/init.sql" \
        "$payload/infra/database/mysql/init.sql"
    install -m 0644 "$PROJECT_ROOT/infra/database/mysql/hardware_handbook.sql" \
        "$payload/infra/database/mysql/hardware_handbook.sql"
    install -m 0755 "$PROJECT_ROOT/scripts/release/server-deploy.sh" \
        "$payload/scripts/server-deploy.sh"

    local -a images=(
        "$backend_release"
        "$frontend_release"
        "$mysql_release"
        "$minio_release"
    )

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
