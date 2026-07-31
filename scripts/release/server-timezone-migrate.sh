#!/usr/bin/env bash
# Guarded one-time conversion from UTC business wall time to Asia/Shanghai.
set -Eeuo pipefail

timezone_usage() {
    cat <<'EOF'
Usage:
  server-timezone-migrate.sh preflight TARGET_DIR
  server-timezone-migrate.sh migrate TARGET_DIR
  server-timezone-migrate.sh rollback TARGET_DIR BACKUP_DIR
EOF
}

timezone_die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
timezone_info() { printf '==> %s\n' "$*"; }

timezone_env_value() {
    local env_file=$1 key=$2
    awk -F= -v expected="$key" '
        $1 == expected { value = substr($0, index($0, "=") + 1) }
        END { sub(/\r$/, "", value); print value }
    ' "$env_file"
}

timezone_ensure_shanghai_runtime_timezone() {
    local target=$1 env_file="$1/.env.docker" pending
    [[ -f "$env_file" && ! -L "$env_file" ]] \
        || timezone_die "runtime environment file is missing or unsafe"
    pending=$(mktemp "$target/.env.docker.tz.XXXXXX")
    chmod 0600 "$pending"
    if ! awk '
        BEGIN { written = 0 }
        /^TZ=/ {
            if (!written) print "TZ=Asia/Shanghai"
            written = 1
            next
        }
        { print }
        END { if (!written) print "TZ=Asia/Shanghai" }
    ' "$env_file" >"$pending"; then
        find "$pending" -maxdepth 0 -delete 2>/dev/null || true
        timezone_die "cannot update the runtime timezone"
    fi
    mv -- "$pending" "$env_file"
    sync -f "$env_file"
    timezone_info "runtime timezone configured as Asia/Shanghai"
}

timezone_require_target() {
    local requested=${1:-}
    [[ -n "$requested" ]] || timezone_die "TARGET_DIR is required"
    [[ -d "$requested" ]] || timezone_die "TARGET_DIR does not exist: $requested"
    TIMEZONE_TARGET=$(realpath -e -- "$requested")
    [[ "$TIMEZONE_TARGET" =~ ^/[A-Za-z0-9._/-]+$ ]] \
        || timezone_die "TARGET_DIR contains unsupported characters"
    [[ -f "$TIMEZONE_TARGET/compose.server.yaml" ]] \
        || timezone_die "missing compose.server.yaml"
    [[ -f "$TIMEZONE_TARGET/.env.docker" ]] \
        || timezone_die "missing .env.docker"
    [[ -f "$TIMEZONE_TARGET/RELEASE" ]] || timezone_die "missing RELEASE"
}

timezone_acquire_lock() {
    local target=$1
    command -v flock >/dev/null || timezone_die "flock is unavailable"
    exec 9>"$target/.timezone-migration.lock"
    flock -n 9 || timezone_die "another timezone maintenance operation is running"
}

timezone_compose() {
    local target=$1
    shift
    docker compose --project-name dwg-agent --project-directory "$target" \
        -f "$target/compose.server.yaml" \
        --env-file "$target/.env.docker" "$@"
}

timezone_mysql_stream() {
    local target=$1 database=${2:-}
    if [[ -n "$database" ]]; then
        timezone_compose "$target" exec -T mysql sh -c \
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=TCP -h 127.0.0.1 -u root "$1"' \
            sh "$database"
    else
        timezone_compose "$target" exec -T mysql sh -c \
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --protocol=TCP -h 127.0.0.1 -u root'
    fi
}

timezone_mysql_scalar() {
    local target=$1 sql=$2 database=${3:-}
    printf '%s\n' "$sql" | timezone_mysql_stream "$target" "$database" | tail -n 1
}

timezone_identifier() {
    [[ "$1" =~ ^[A-Za-z0-9_]+$ ]] \
        || timezone_die "unsafe MySQL identifier: $1"
}

timezone_table_exists() {
    local target=$1 table=$2 database count
    timezone_identifier "$table"
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    timezone_identifier "$database"
    count=$(timezone_mysql_scalar "$target" \
        "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='$database' AND TABLE_NAME='$table'") \
        || timezone_die "metadata query failed while checking table: $table"
    case "$count" in
        1) return 0 ;;
        0) return 1 ;;
        *) timezone_die "invalid metadata result while checking table $table: $count" ;;
    esac
}

timezone_require_zero() {
    local target=$1 table=$2 predicate=$3 label=$4 presence=${5:-required} count
    if ! timezone_table_exists "$target" "$table"; then
        if [[ "$presence" == optional ]]; then
            timezone_info "$label: optional table not present at the pre-migration revision"
            return 0
        fi
        timezone_die "$label cannot be checked because required table is missing: $table"
    fi
    count=$(timezone_mysql_scalar "$target" \
        "SELECT COUNT(*) FROM \`$table\` WHERE $predicate" \
        "$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)")
    [[ "$count" =~ ^[0-9]+$ ]] || timezone_die "$label count is invalid"
    [[ "$count" == 0 ]] || timezone_die "$label must be zero; found $count"
    timezone_info "$label: 0"
}

timezone_require_quiescent() {
    local target=$1
    timezone_require_zero "$target" file_transfers \
        "status IN ('prepared','in_progress','failed','compensation_required')" \
        "active or unresolved file_transfers"
    timezone_require_zero "$target" jobs \
        "status IN ('pending','queued','running','validating','waiting_cad_worker')" \
        "active jobs"
    timezone_require_zero "$target" job_dispatches \
        "status IN ('pending','leased','failed')" \
        "active job_dispatches" optional
    timezone_require_zero "$target" workflow_input_upload_sessions \
        "status IN ('open','uploading','ready','finalizing')" \
        "active upload_sessions" optional
}

timezone_require_complete_inputs() {
    local target=$1 database incomplete complete excel_audits folder_audits minimum
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    incomplete=$(timezone_mysql_scalar "$target" "
        SELECT COUNT(*)
        FROM workflow_input_batches b
        WHERE b.status IN ('uploading','active')
          AND (
            NOT EXISTS (
              SELECT 1 FROM workflow_input_items i
              WHERE i.input_batch_id=b.id AND i.role='source_excel'
                AND i.status NOT IN ('failed','removed')
            )
            OR NOT EXISTS (
              SELECT 1 FROM workflow_input_items i
              WHERE i.input_batch_id=b.id AND i.role='source_dwg'
                AND i.status NOT IN ('failed','removed')
            )
          )" "$database")
    [[ "$incomplete" == 0 ]] \
        || timezone_die "workflow input batches are incomplete: $incomplete"
    complete=$(timezone_mysql_scalar "$target" "
        SELECT COUNT(*)
        FROM workflow_input_batches b
        WHERE EXISTS (
          SELECT 1 FROM workflow_input_items i
          WHERE i.input_batch_id=b.id AND i.role='source_excel'
            AND i.status NOT IN ('failed','removed')
        )
        AND EXISTS (
          SELECT 1 FROM workflow_input_items i
          WHERE i.input_batch_id=b.id AND i.role='source_dwg'
            AND i.status NOT IN ('failed','removed')
        )" "$database")
    minimum=${TIMEZONE_MIN_COMPLETE_WORKFLOWS:-2}
    [[ "$minimum" =~ ^[1-9][0-9]*$ ]] \
        || timezone_die "TIMEZONE_MIN_COMPLETE_WORKFLOWS must be positive"
    (( complete >= minimum )) \
        || timezone_die "complete workflow inputs: $complete; required: $minimum"
    excel_audits=$(timezone_mysql_scalar "$target" \
        "SELECT COUNT(*) FROM audit_logs WHERE action='workflow_input_excel.import'" \
        "$database")
    folder_audits=$(timezone_mysql_scalar "$target" \
        "SELECT COUNT(*) FROM audit_logs WHERE action='workflow_input_dwg_folders.import'" \
        "$database")
    (( excel_audits >= minimum && folder_audits >= minimum )) \
        || timezone_die "workflow input audit evidence is incomplete"
    timezone_info "complete workflow inputs: $complete; Excel audits: $excel_audits; DWG-folder audits: $folder_audits"
}

timezone_require_datetime_audit() {
    local target=$1 database total business protocol
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    total=$(timezone_mysql_scalar "$target" \
        "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='$database' AND DATA_TYPE='datetime'")
    protocol=$(timezone_mysql_scalar "$target" "
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA='$database' AND DATA_TYPE='datetime'
          AND ((TABLE_NAME='kombu_message' AND COLUMN_NAME='timestamp')
            OR (TABLE_NAME='celery_taskmeta' AND COLUMN_NAME='date_done')
            OR (TABLE_NAME='celery_tasksetmeta' AND COLUMN_NAME='date_done'))")
    business=$((total - protocol))
    [[ "$total" == 129 && "$business" == 126 && "$protocol" == 3 ]] \
        || timezone_die "DATETIME audit drifted: total=$total business=$business celery_utc=$protocol"
    timezone_info "DATETIME audit: total=129 business=126 celery_utc=3"
}

timezone_require_pre_migration_head() {
    local target=$1 database head
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    head=$(timezone_mysql_scalar "$target" 'SELECT version_num FROM alembic_version' "$database")
    [[ "$head" == d1e7f3a9c520 ]] \
        || timezone_die "timezone migration requires pre-migration head d1e7f3a9c520; found $head"
    timezone_info "pre-migration Alembic head: d1e7f3a9c520"
}

timezone_require_disk_space() {
    local target=$1 docker_root minimum_gib docker_kib backup_kib database estimated required docker_required
    minimum_gib=$(timezone_env_value "$target/.env.docker" DOCKER_MIN_FREE_GIB)
    minimum_gib=${minimum_gib:-20}
    [[ "$minimum_gib" =~ ^[1-9][0-9]*$ ]] \
        || timezone_die "DOCKER_MIN_FREE_GIB must be positive"
    docker_root=$(docker info --format '{{.DockerRootDir}}') \
        || timezone_die "cannot determine Docker data root"
    docker_kib=$(df -Pk -- "$docker_root" | awk 'END {print $4}')
    backup_kib=$(df -Pk -- "$target" | awk 'END {print $4}')
    (( docker_kib >= minimum_gib * 1024 * 1024 )) \
        || timezone_die "Docker data root has insufficient free space"
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    estimated=$(timezone_mysql_scalar "$target" "
        SELECT COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH),0)
        FROM information_schema.TABLES WHERE TABLE_SCHEMA='$database'")
    [[ "$estimated" =~ ^[0-9]+$ ]] || timezone_die "database size estimate is invalid"
    docker_required=$((estimated * 2 / 1024 + 1024 * 1024))
    if (( docker_required < minimum_gib * 1024 * 1024 )); then
        docker_required=$((minimum_gib * 1024 * 1024))
    fi
    (( docker_kib >= docker_required )) \
        || timezone_die "Docker data root lacks room for the verified temporary database copy"
    required=$((estimated * 3 / 1024 + 1024 * 1024))
    (( backup_kib >= required )) || timezone_die "backup filesystem has insufficient free space"
    timezone_info "disk gate passed: Docker root and backup filesystem have database-sized headroom"
}

timezone_report_baseline() {
    local target=$1 database table count release minio_usage
    local -a core_tables=(
        sys_users projects files file_transfers workflow_runs jobs audit_logs
    )
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    release=$(tr -d '\r\n' <"$target/RELEASE")
    [[ -n "$release" ]] || timezone_die "installed RELEASE is empty"
    timezone_info "baseline RELEASE: $release"
    for table in "${core_tables[@]}"; do
        count=$(timezone_mysql_scalar "$target" "SELECT COUNT(*) FROM \`$table\`" "$database")
        [[ "$count" =~ ^[0-9]+$ ]] || timezone_die "baseline count is invalid for $table"
        timezone_info "baseline MySQL rows $table: $count"
    done
    minio_usage=$(timezone_compose "$target" exec -T minio /usr/bin/mc du --json local) \
        || timezone_die "MinIO baseline usage query failed"
    [[ -n "$minio_usage" ]] || timezone_die "MinIO baseline usage is empty"
    timezone_info "baseline MinIO object count and bytes: $minio_usage"
}

timezone_require_services_healthy() {
    local target=$1 rows service line state health
    local -a services
    mapfile -t services < <(timezone_compose "$target" config --services)
    (( ${#services[@]} == 15 || ${#services[@]} == 16 )) \
        || timezone_die "expected 15 or 16 services; found ${#services[@]}"
    rows=$(timezone_compose "$target" ps --all --format '{{.Service}}|{{.State}}|{{.Health}}')
    for service in "${services[@]}"; do
        line=$(awk -F'|' -v expected="$service" '$1 == expected {print; exit}' <<<"$rows")
        if [[ -z "$line" && "$service" == dispatcher ]]; then
            timezone_info "dispatcher is new in this release and will start after migration"
            continue
        fi
        [[ -n "$line" ]] || timezone_die "service is missing: $service"
        IFS='|' read -r _ state health <<<"$line"
        [[ "$state" == running && ( -z "$health" || "$health" == healthy ) ]] \
            || timezone_die "service is not healthy: $service state=$state health=$health"
    done
    timezone_info "current service health gate passed"
}

timezone_preflight() {
    local target=${1:-}
    timezone_require_target "$target"
    target=$TIMEZONE_TARGET
    command -v docker >/dev/null || timezone_die "docker is unavailable"
    command -v gzip >/dev/null || timezone_die "gzip is unavailable"
    command -v sha256sum >/dev/null || timezone_die "sha256sum is unavailable"
    command -v sync >/dev/null || timezone_die "sync is unavailable"
    timezone_compose "$target" config --quiet
    timezone_require_services_healthy "$target"
    timezone_require_disk_space "$target"
    timezone_report_baseline "$target"
    timezone_require_complete_inputs "$target"
    timezone_require_quiescent "$target"
    timezone_require_datetime_audit "$target"
    timezone_require_pre_migration_head "$target"
    timezone_info "PREFLIGHT PASS"
}

timezone_wait_services() {
    local target=$1 timeout=$2
    shift 2
    local -a services=("$@")
    local deadline rows service line state health ready
    deadline=$((SECONDS + timeout))
    while true; do
        rows=$(timezone_compose "$target" ps --all \
            --format '{{.Service}}|{{.State}}|{{.Health}}' "${services[@]}")
        ready=0
        for service in "${services[@]}"; do
            line=$(awk -F'|' -v expected="$service" '$1 == expected {print; exit}' <<<"$rows")
            if [[ -n "$line" ]]; then
                IFS='|' read -r _ state health <<<"$line"
                if [[ "$state" == running && ( -z "$health" || "$health" == healthy ) ]]; then
                    ready=$((ready + 1))
                fi
            fi
        done
        (( ready == ${#services[@]} )) && return 0
        (( SECONDS < deadline )) || timezone_die "service readiness timeout: ${services[*]}"
        sleep 2
    done
}

timezone_drop_verify_schema() {
    local target=${1:-}
    if [[ -n "${TIMEZONE_VERIFY_SCHEMA:-}" \
        && "$TIMEZONE_VERIFY_SCHEMA" =~ ^dwg_agent_timezone_verify_[0-9]+_[0-9]+$ ]]; then
        printf 'DROP DATABASE IF EXISTS `%s`;\n' "$TIMEZONE_VERIFY_SCHEMA" \
            | timezone_mysql_stream "$target" >/dev/null || true
        TIMEZONE_VERIFY_SCHEMA=""
    fi
}

timezone_create_backup() {
    local target=$1 backup=$2 database dump previous core_table original restored dump_sha marker_tmp original_head
    local counts_sha minio_summary_sha previous_compose_sha previous_env_sha previous_release_sha previous_images_sha
    local -a core_tables=(
        sys_users projects files file_transfers workflow_runs jobs audit_logs
    )
    umask 077
    mkdir -m 0700 -- "$backup"
    install -m 0644 "$target/compose.server.yaml" "$backup/compose.server.new.yaml"
    install -m 0644 "$target/RELEASE" "$backup/RELEASE.new"
    install -m 0600 "$target/.env.docker" "$backup/.env.docker.new"
    previous="$target/.rollback-candidate"
    [[ -f "$previous/compose.server.yaml" && -f "$previous/.env.docker" \
        && -f "$previous/RELEASE" && -f "$previous/images.manifest" ]] \
        || timezone_die "rollback candidate from the previous release is missing"
    install -m 0644 "$previous/compose.server.yaml" "$backup/compose.server.previous.yaml"
    install -m 0600 "$previous/.env.docker" "$backup/.env.docker.previous"
    install -m 0644 "$previous/RELEASE" "$backup/RELEASE.previous"
    install -m 0644 "$previous/images.manifest" "$backup/images.manifest.previous"

    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    timezone_identifier "$database"
    original_head=$(timezone_mysql_scalar "$target" 'SELECT version_num FROM alembic_version' "$database")
    [[ "$original_head" == d1e7f3a9c520 ]] \
        || timezone_die "database revision changed before backup: $original_head"
    dump="$backup/mysql-before.sql.gz"
    timezone_compose "$target" exec -T mysql sh -c \
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump --protocol=TCP -h 127.0.0.1 -u root --single-transaction --routines --triggers --events "$MYSQL_DATABASE"' \
        | gzip -9 >"$dump"
    chmod 0600 "$dump"
    gzip -t "$dump"
    (cd "$backup" && sha256sum mysql-before.sql.gz > mysql-before.sql.gz.sha256)
    (cd "$backup" && sha256sum -c mysql-before.sql.gz.sha256 >/dev/null) \
        || timezone_die "backup checksum verification failed"

    {
        printf 'source_database\t%s\n' "$database"
        printf 'alembic_head\t%s\n' "$original_head"
        for core_table in "${core_tables[@]}"; do
            original=$(timezone_mysql_scalar "$target" "SELECT COUNT(*) FROM \`$core_table\`" "$database")
            printf '%s\t%s\n' "$core_table" "$original"
        done
        printf 'file_bytes\t%s\n' "$(timezone_mysql_scalar "$target" 'SELECT COALESCE(SUM(size_bytes),0) FROM files' "$database")"
    } >"$backup/pre-migration-counts.tsv"
    chmod 0600 "$backup/pre-migration-counts.tsv"
    timezone_compose "$target" exec -T minio \
        /usr/bin/mc du --recursive --json local >"$backup/minio-before.jsonl"
    chmod 0600 "$backup/minio-before.jsonl"
    timezone_compose "$target" exec -T minio \
        /usr/bin/mc du --json local >"$backup/minio-summary-before.json"
    [[ -s "$backup/minio-summary-before.json" ]] \
        || timezone_die "MinIO summary backup is empty"
    chmod 0600 "$backup/minio-summary-before.json"

    TIMEZONE_VERIFY_SCHEMA="dwg_agent_timezone_verify_$(date +%Y%m%d%H%M%S)_$$"
    timezone_identifier "$TIMEZONE_VERIFY_SCHEMA"
    printf 'CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n' \
        "$TIMEZONE_VERIFY_SCHEMA" | timezone_mysql_stream "$target" >/dev/null
    gzip -dc "$dump" | timezone_mysql_stream "$target" "$TIMEZONE_VERIFY_SCHEMA"
    for core_table in "${core_tables[@]}"; do
        original=$(timezone_mysql_scalar "$target" "SELECT COUNT(*) FROM \`$core_table\`" "$database")
        restored=$(timezone_mysql_scalar "$target" "SELECT COUNT(*) FROM \`$core_table\`" "$TIMEZONE_VERIFY_SCHEMA")
        [[ "$original" == "$restored" ]] \
            || timezone_die "restored row count mismatch for $core_table"
    done
    timezone_drop_verify_schema "$target"
    sync -f "$dump"
    sync -f "$backup"
    dump_sha=$(sha256sum "$dump" | awk '{print $1}')
    counts_sha=$(sha256sum "$backup/pre-migration-counts.tsv" | awk '{print $1}')
    minio_summary_sha=$(sha256sum "$backup/minio-summary-before.json" | awk '{print $1}')
    previous_compose_sha=$(sha256sum "$backup/compose.server.previous.yaml" | awk '{print $1}')
    previous_env_sha=$(sha256sum "$backup/.env.docker.previous" | awk '{print $1}')
    previous_release_sha=$(sha256sum "$backup/RELEASE.previous" | awk '{print $1}')
    previous_images_sha=$(sha256sum "$backup/images.manifest.previous" | awk '{print $1}')
    for checksum in "$dump_sha" "$counts_sha" "$minio_summary_sha" \
        "$previous_compose_sha" "$previous_env_sha" "$previous_release_sha" \
        "$previous_images_sha"; do
        [[ "$checksum" =~ ^[0-9a-f]{64}$ ]] \
            || timezone_die "backup metadata checksum generation failed"
    done
    marker_tmp="$backup/.VERIFIED.tmp"
    {
        printf 'VERIFIED_BACKUP_V1\n'
        printf 'target\t%s\n' "$target"
        printf 'database\t%s\n' "$database"
        printf 'dump_sha256\t%s\n' "$dump_sha"
        printf 'pre_migration_head\t%s\n' "$original_head"
        printf 'counts_sha256\t%s\n' "$counts_sha"
        printf 'minio_summary_sha256\t%s\n' "$minio_summary_sha"
        printf 'previous_compose_sha256\t%s\n' "$previous_compose_sha"
        printf 'previous_env_sha256\t%s\n' "$previous_env_sha"
        printf 'previous_release_sha256\t%s\n' "$previous_release_sha"
        printf 'previous_images_sha256\t%s\n' "$previous_images_sha"
    } >"$marker_tmp"
    chmod 0600 "$marker_tmp"
    mv -- "$marker_tmp" "$backup/VERIFIED"
    sync -f "$backup/VERIFIED"
    sync -f "$backup"
    timezone_info "backup, checksum and temporary-schema restore verification passed: $backup"
}

timezone_require_verified_backup() {
    local target=$1 backup=$2 database dump_sha marker_target marker_database marker_sha marker_head
    [[ -f "$backup/VERIFIED" && ! -L "$backup/VERIFIED" ]] \
        || timezone_die "backup has no verified-complete marker"
    [[ $(sed -n '1p' "$backup/VERIFIED") == VERIFIED_BACKUP_V1 ]] \
        || timezone_die "backup verification marker version is invalid"
    marker_target=$(awk -F'\t' '$1 == "target" {print $2}' "$backup/VERIFIED")
    marker_database=$(awk -F'\t' '$1 == "database" {print $2}' "$backup/VERIFIED")
    marker_sha=$(awk -F'\t' '$1 == "dump_sha256" {print $2}' "$backup/VERIFIED")
    marker_head=$(awk -F'\t' '$1 == "pre_migration_head" {print $2}' "$backup/VERIFIED")
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    dump_sha=$(sha256sum "$backup/mysql-before.sql.gz" | awk '{print $1}')
    [[ "$marker_target" == "$target" ]] || timezone_die "backup marker belongs to another target"
    [[ "$marker_database" == "$database" ]] || timezone_die "backup marker database does not match"
    [[ "$marker_sha" == "$dump_sha" ]] || timezone_die "backup marker dump checksum does not match"
    [[ "$marker_head" == d1e7f3a9c520 ]] || timezone_die "backup marker has an invalid pre-migration head"
    timezone_require_marker_file_hash "$backup/VERIFIED" counts_sha256 \
        "$backup/pre-migration-counts.tsv"
    timezone_require_marker_file_hash "$backup/VERIFIED" minio_summary_sha256 \
        "$backup/minio-summary-before.json"
    timezone_require_marker_file_hash "$backup/VERIFIED" previous_compose_sha256 \
        "$backup/compose.server.previous.yaml"
    timezone_require_marker_file_hash "$backup/VERIFIED" previous_env_sha256 \
        "$backup/.env.docker.previous"
    timezone_require_marker_file_hash "$backup/VERIFIED" previous_release_sha256 \
        "$backup/RELEASE.previous"
    timezone_require_marker_file_hash "$backup/VERIFIED" previous_images_sha256 \
        "$backup/images.manifest.previous"
}

timezone_require_marker_file_hash() {
    local marker=$1 key=$2 file=$3 expected actual
    expected=$(awk -F'\t' -v wanted="$key" '$1 == wanted {print $2}' "$marker")
    actual=$(sha256sum "$file" | awk '{print $1}')
    [[ "$expected" =~ ^[0-9a-f]{64}$ && "$actual" == "$expected" ]] \
        || timezone_die "backup marker hash mismatch for: $(basename -- "$file")"
}

timezone_verify_minio_unchanged() {
    local target=$1 backup=$2 current
    current=$(timezone_compose "$target" exec -T minio /usr/bin/mc du --json local) \
        || timezone_die "MinIO usage verification failed"
    [[ "$current" == "$(<"$backup/minio-summary-before.json")" ]] \
        || timezone_die "MinIO object count or bytes changed during maintenance"
    timezone_info "MinIO object count and bytes match the verified backup baseline"
}

timezone_require_previous_runtime() {
    local target=$1 backup=$2 image_ref expected_id actual_id service_count image_count=0
    docker compose --project-name dwg-agent --project-directory "$target" \
        -f "$backup/compose.server.previous.yaml" \
        --env-file "$backup/.env.docker.previous" config --quiet
    service_count=$(docker compose --project-name dwg-agent --project-directory "$target" \
        -f "$backup/compose.server.previous.yaml" \
        --env-file "$backup/.env.docker.previous" config --services | wc -l)
    [[ "$service_count" == 15 ]] \
        || timezone_die "previous runtime must contain exactly 15 services; found $service_count"
    while IFS=$'\t' read -r image_ref expected_id; do
        [[ -n "$image_ref" && -n "$expected_id" ]] \
            || timezone_die "previous image manifest contains an invalid row"
        actual_id=$(docker image inspect "$image_ref" --format '{{.Id}}') \
            || timezone_die "previous image is unavailable: $image_ref"
        [[ "$actual_id" == "$expected_id" ]] \
            || timezone_die "previous image ID mismatch: $image_ref"
        image_count=$((image_count + 1))
    done <"$backup/images.manifest.previous"
    (( image_count == 4 )) || timezone_die "previous image manifest must contain exactly four images"
    timezone_info "previous compose and $image_count pinned images are available for rollback"
}

timezone_application_services() {
    local target=$1 service
    while IFS= read -r service; do
        case "$service" in
            mysql|minio|nginx) ;;
            *) printf '%s\n' "$service" ;;
        esac
    done < <(timezone_compose "$target" config --services)
}

timezone_service_exists() {
    local target=$1 expected=$2 services
    services=$(timezone_compose "$target" config --services) \
        || timezone_die "cannot enumerate Compose services"
    grep -Fxq -- "$expected" <<<"$services"
}

timezone_verify_database_runtime() {
    local target=$1 database head session_zone delta app_offset
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    head=$(timezone_mysql_scalar "$target" 'SELECT version_num FROM alembic_version' "$database")
    session_zone=$(timezone_mysql_scalar "$target" 'SELECT @@session.time_zone' "$database")
    delta=$(timezone_mysql_scalar "$target" 'SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())' "$database")
    timezone_compose "$target" exec -T backend-api sh -c '
        set -eu
        current=$(alembic current 2>/dev/null | awk "NF {print \$1; exit}")
        expected=$(alembic heads 2>/dev/null | awk "NF {print \$1; exit}")
        test -n "$current"
        test "$current" = "$expected"
        alembic history 2>/dev/null | grep -q a4c8e1f2b730
    '
    [[ "$session_zone" == +08:00 ]] || timezone_die "unexpected MySQL session timezone: $session_zone"
    (( delta >= 28798 && delta <= 28802 )) || timezone_die "MySQL UTC offset is invalid: $delta"
    app_offset=$(timezone_compose "$target" exec -T backend-api date +%z | tr -d '\r')
    [[ "$app_offset" == +0800 ]] \
        || timezone_die "application container timezone is invalid: $app_offset"
    timezone_info "Alembic head $head includes a4c8e1f2b730; MySQL and application timezone are +08:00"
}

timezone_verify_rollback_database_runtime() {
    local target=$1 database head session_zone delta
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    head=$(timezone_mysql_scalar "$target" 'SELECT version_num FROM alembic_version' "$database")
    session_zone=$(timezone_mysql_scalar "$target" 'SELECT @@session.time_zone' "$database")
    delta=$(timezone_mysql_scalar "$target" \
        'SELECT TIMESTAMPDIFF(SECOND, UTC_TIMESTAMP(), NOW())' "$database")
    timezone_compose "$target" exec -T backend-api sh -c '
        set -eu
        current=$(alembic current 2>/dev/null | awk "NF {print \$1; exit}")
        expected=$(alembic heads 2>/dev/null | awk "NF {print \$1; exit}")
        test "$current" = d1e7f3a9c520
        test "$expected" = d1e7f3a9c520
    '
    [[ "$head" == d1e7f3a9c520 ]] \
        || timezone_die "rollback database revision is invalid: $head"
    (( delta >= -2 && delta <= 2 )) \
        || timezone_die "rollback MySQL wall clock is not UTC: zone=$session_zone delta=$delta"
    timezone_info "rollback Alembic head is d1e7f3a9c520 and MySQL wall clock is UTC"
}

timezone_verify_preserved_counts() {
    local target=$1 backup=$2 label=$3 database item expected actual required
    local -A seen=()
    local -a required_items=(
        source_database alembic_head sys_users projects files file_transfers
        workflow_runs jobs audit_logs file_bytes
    )
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    while IFS=$'\t' read -r item expected; do
        [[ -n "$item" && -z "${seen[$item]:-}" ]] \
            || timezone_die "$label manifest contains an empty or duplicate key: $item"
        seen[$item]=1
        case "$item" in
            source_database)
                [[ "$expected" == "$database" ]] \
                    || timezone_die "$label manifest database mismatch"
                ;;
            alembic_head)
                [[ "$expected" == d1e7f3a9c520 ]] \
                    || timezone_die "$label manifest pre-migration head mismatch"
                ;;
            sys_users|projects|files|file_transfers|workflow_runs|jobs|audit_logs)
                actual=$(timezone_mysql_scalar "$target" \
                    "SELECT COUNT(*) FROM \`$item\`" "$database")
                [[ "$actual" == "$expected" ]] \
                    || timezone_die "$label row count mismatch for $item: expected=$expected actual=$actual"
                ;;
            file_bytes)
                actual=$(timezone_mysql_scalar "$target" \
                    'SELECT COALESCE(SUM(size_bytes),0) FROM files' "$database")
                [[ "$actual" == "$expected" ]] \
                    || timezone_die "$label file byte count mismatch: expected=$expected actual=$actual"
                ;;
            *) timezone_die "$label manifest contains an unknown key: $item" ;;
        esac
    done <"$backup/pre-migration-counts.tsv"
    for required in "${required_items[@]}"; do
        [[ -n "${seen[$required]:-}" ]] \
            || timezone_die "$label manifest is missing required key: $required"
    done
    timezone_info "$label core-table rows and file bytes match the pre-migration manifest"
}

timezone_verify_migrated_counts() {
    timezone_verify_preserved_counts "$1" "$2" "migration"
}

timezone_verify_http_runtime() {
    local target=$1 port
    port=$(timezone_env_value "$target/.env.docker" HTTP_PORT)
    port=${port:-80}
    curl -fsS "http://127.0.0.1:${port}/nginx-health" >/dev/null
    curl -fsS "http://127.0.0.1:${port}/health/ready" >/dev/null
    timezone_info "HTTP readiness passed"
}

timezone_migrate() {
    local target=${1:-} backup timestamp service
    local -a application workers
    timezone_require_target "$target"
    target=$TIMEZONE_TARGET
    timezone_acquire_lock "$target"
    timezone_preflight "$target"
    TIMEZONE_MAINTENANCE_ACTIVE=1
    timezone_compose "$target" stop -t 180 nginx
    timezone_require_complete_inputs "$target"
    timezone_require_quiescent "$target"
    timezone_require_datetime_audit "$target"
    timezone_require_pre_migration_head "$target"
    timezone_ensure_shanghai_runtime_timezone "$target"
    mapfile -t application < <(timezone_application_services "$target")
    timezone_compose "$target" stop -t 180 "${application[@]}"
    timestamp=$(date +%Y%m%d-%H%M%S)
    mkdir -p -- "$target/backups"
    chmod 0750 "$target/backups"
    backup="$target/backups/timezone-$timestamp"
    [[ ! -e "$backup" ]] || timezone_die "backup directory already exists: $backup"
    timezone_create_backup "$target" "$backup"
    timezone_require_previous_runtime "$target" "$backup"

    timezone_compose "$target" up -d --no-build --force-recreate mysql minio
    timezone_wait_services "$target" 240 mysql minio
    timezone_compose "$target" up -d --no-build --force-recreate backend-api
    timezone_wait_services "$target" 300 backend-api
    timezone_verify_database_runtime "$target"
    timezone_verify_migrated_counts "$target" "$backup"
    timezone_verify_minio_unchanged "$target" "$backup"
    if timezone_service_exists "$target" dispatcher; then
        timezone_compose "$target" up -d --no-build --force-recreate dispatcher
        timezone_wait_services "$target" 180 dispatcher
    fi
    workers=()
    for service in "${application[@]}"; do
        [[ "$service" == backend-api || "$service" == dispatcher ]] || workers+=("$service")
    done
    if (( ${#workers[@]} )); then
        timezone_compose "$target" up -d --no-build --force-recreate "${workers[@]}"
        timezone_wait_services "$target" 360 "${workers[@]}"
    fi
    timezone_compose "$target" up -d --no-build --force-recreate nginx
    timezone_wait_services "$target" 180 nginx
    timezone_verify_http_runtime "$target"
    printf '%s\n' "$backup" >"$target/backups/LAST_TIMEZONE_BACKUP"
    chmod 0600 "$target/backups/LAST_TIMEZONE_BACKUP"
    TIMEZONE_MAINTENANCE_ACTIVE=0
    timezone_info "MIGRATION PASS; rollback backup: $backup"
}

timezone_restore_database() {
    local target=$1 backup=$2 database dump
    dump="$backup/mysql-before.sql.gz"
    (cd "$backup" && sha256sum -c mysql-before.sql.gz.sha256)
    gzip -t "$dump"
    database=$(timezone_env_value "$target/.env.docker" MYSQL_DATABASE)
    timezone_identifier "$database"
    printf 'DROP DATABASE IF EXISTS `%s`; CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n' \
        "$database" "$database" | timezone_mysql_stream "$target" >/dev/null
    gzip -dc "$dump" | timezone_mysql_stream "$target" "$database"
}

timezone_verify_restored_counts() {
    timezone_verify_preserved_counts "$1" "$2" "rollback"
}

timezone_rollback() {
    local target=${1:-} requested_backup=${2:-} backup backup_parent backup_name service
    local -a services workers
    timezone_require_target "$target"
    target=$TIMEZONE_TARGET
    timezone_acquire_lock "$target"
    [[ "$requested_backup" == /* ]] \
        || timezone_die "BACKUP_DIR must be an absolute path"
    [[ -n "$requested_backup" && -d "$requested_backup" ]] \
        || timezone_die "BACKUP_DIR is required and must exist"
    backup=$(realpath -e -- "$requested_backup")
    backup_parent=$(dirname -- "$backup")
    backup_name=$(basename -- "$backup")
    [[ "$backup_parent" == "$target/backups" \
        && "$backup_name" =~ ^timezone-[0-9]{8}-[0-9]{6}$ ]] \
        || timezone_die "BACKUP_DIR is outside the guarded timezone backup root"
    for required in compose.server.previous.yaml .env.docker.previous RELEASE.previous \
        images.manifest.previous VERIFIED \
        mysql-before.sql.gz mysql-before.sql.gz.sha256 pre-migration-counts.tsv \
        minio-summary-before.json; do
        [[ -f "$backup/$required" ]] || timezone_die "backup file is missing: $required"
    done
    timezone_require_verified_backup "$target" "$backup"
    timezone_require_previous_runtime "$target" "$backup"
    (cd "$backup" && sha256sum -c mysql-before.sql.gz.sha256)
    gzip -t "$backup/mysql-before.sql.gz"
    TIMEZONE_MAINTENANCE_ACTIVE=1
    timezone_compose "$target" stop -t 180 nginx
    timezone_require_quiescent "$target"
    services=()
    while IFS= read -r service; do
        [[ "$service" == nginx ]] || services+=("$service")
    done < <(timezone_compose "$target" config --services)
    timezone_compose "$target" stop -t 180 "${services[@]}"
    install -m 0644 "$backup/compose.server.previous.yaml" "$target/compose.server.yaml"
    install -m 0600 "$backup/.env.docker.previous" "$target/.env.docker"
    [[ -f "$backup/RELEASE.previous" ]] \
        && install -m 0644 "$backup/RELEASE.previous" "$target/RELEASE"
    [[ -f "$backup/images.manifest.previous" ]] \
        && install -m 0644 "$backup/images.manifest.previous" "$target/images.manifest"
    timezone_compose "$target" up -d --no-build --force-recreate mysql minio
    timezone_wait_services "$target" 240 mysql minio
    timezone_verify_minio_unchanged "$target" "$backup"
    timezone_restore_database "$target" "$backup"
    timezone_verify_restored_counts "$target" "$backup"
    timezone_compose "$target" up -d --no-build --force-recreate backend-api
    timezone_wait_services "$target" 300 backend-api
    timezone_verify_rollback_database_runtime "$target"
    workers=()
    while IFS= read -r service; do
        case "$service" in
            mysql|minio|nginx|backend-api) ;;
            *) workers+=("$service") ;;
        esac
    done < <(timezone_compose "$target" config --services)
    if (( ${#workers[@]} )); then
        timezone_compose "$target" up -d --no-build --force-recreate "${workers[@]}"
        timezone_wait_services "$target" 360 "${workers[@]}"
    fi
    timezone_compose "$target" up -d --no-build --force-recreate nginx
    timezone_wait_services "$target" 180 nginx
    local port
    port=$(timezone_env_value "$target/.env.docker" HTTP_PORT)
    port=${port:-80}
    curl -fsS "http://127.0.0.1:${port}/nginx-health" >/dev/null
    curl -fsS "http://127.0.0.1:${port}/health/ready" >/dev/null
    TIMEZONE_MAINTENANCE_ACTIVE=0
    timezone_info "ROLLBACK PASS: restored $backup"
}

TIMEZONE_TARGET=""
TIMEZONE_VERIFY_SCHEMA=""
TIMEZONE_MAINTENANCE_ACTIVE=0

timezone_fail_closed() {
    local status=$? service
    local -a services=()
    trap - EXIT
    set +e
    if (( status != 0 && TIMEZONE_MAINTENANCE_ACTIVE == 1 )) \
        && [[ -n "${TIMEZONE_TARGET:-}" ]]; then
        while IFS= read -r service; do
            case "$service" in
                mysql|minio) ;;
                *) services+=("$service") ;;
            esac
        done < <(timezone_compose "$TIMEZONE_TARGET" config --services 2>/dev/null)
        if (( ${#services[@]} )); then
            timezone_compose "$TIMEZONE_TARGET" stop -t 60 "${services[@]}" >/dev/null 2>&1 || true
        fi
        printf 'ERROR: maintenance failed closed; gateway and application writers were stopped\n' >&2
    fi
    timezone_drop_verify_schema "${TIMEZONE_TARGET:-}"
    exit "$status"
}

trap timezone_fail_closed EXIT

case "${1:-}" in
    preflight) shift; timezone_preflight "$@" ;;
    migrate) shift; timezone_migrate "$@" ;;
    rollback) shift; timezone_rollback "$@" ;;
    -h|--help|"") timezone_usage ;;
    *) timezone_usage; timezone_die "unknown command: $1" ;;
esac
