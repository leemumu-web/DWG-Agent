from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import yaml

from tests.support.paths import REPO_ROOT

RENDERER = REPO_ROOT / "scripts/release/render_server_compose.py"
RELEASE_SCRIPT = REPO_ROOT / "scripts/release.sh"
SERVER_SCRIPT = REPO_ROOT / "scripts/release/server-deploy.sh"
TIMEZONE_SCRIPT = REPO_ROOT / "scripts/release/server-timezone-migrate.sh"
ARCHIVE_VERIFIER = REPO_ROOT / "scripts/release/verify_image_archive.py"
LIVE_REMNANT_VERIFIER = REPO_ROOT / "scripts/release/verify_live_remnant.py"
RUNTIME_FEATURE_VERIFIER = REPO_ROOT / "scripts/release/verify_runtime_features.py"
ODA_SMOKE_FIXTURE = REPO_ROOT / "scripts/release/fixtures/oda_runtime_smoke.dxf"
PYPROJECT = REPO_ROOT / "backend" / "pyproject.toml"


def test_runtime_feature_verifier_bootstraps_application_imports_from_any_cwd(tmp_path):
    content = RUNTIME_FEATURE_VERIFIER.read_text(encoding="utf-8")
    assert "Path(__file__).resolve().parents[2]" in content
    assert "sys.path.insert" in content

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "DXF_PIPELINE_ENABLED": "true",
            "DXF2DWG_PIPELINE_ENABLED": "true",
            "DXF2EXCEL_PIPELINE_ENABLED": "false",
            "DXF_CLASSIFICATION_PIPELINE_ENABLED": "true",
            "DXF_SPLIT_PIPELINE_ENABLED": "true",
            "EXCEL_FINAL_PIPELINE_ENABLED": "true",
            "REMNANT_INVENTORY_ENABLED": "true",
        }
    )

    result = subprocess.run(
        [sys.executable, str(RUNTIME_FEATURE_VERIFIER)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def _write_legacy_image_archive(
    archive: Path,
    *,
    image: str,
    layer_members: tuple[str, ...],
) -> None:
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w") as layer:
        for member_name in layer_members:
            payload = b"test payload"
            member = tarfile.TarInfo(member_name)
            member.size = len(payload)
            layer.addfile(member, io.BytesIO(payload))
    layer_payload = layer_buffer.getvalue()
    manifest_payload = json.dumps(
        [{"Config": "config.json", "RepoTags": [image], "Layers": ["layer/layer.tar"]}]
    ).encode()

    with tarfile.open(archive, mode="w") as outer:
        for member_name, payload in (
            ("manifest.json", manifest_payload),
            ("layer/layer.tar", layer_payload),
        ):
            member = tarfile.TarInfo(member_name)
            member.size = len(payload)
            outer.addfile(member, io.BytesIO(payload))


def _write_nested_oci_image_archive(archive: Path, *, image: str, member_name: str) -> None:
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w") as layer:
        payload = b"compiled payload"
        member = tarfile.TarInfo(member_name)
        member.size = len(payload)
        layer.addfile(member, io.BytesIO(payload))
    layer_payload = layer_buffer.getvalue()

    def descriptor(payload: bytes, media_type: str) -> dict[str, object]:
        return {
            "mediaType": media_type,
            "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
            "size": len(payload),
        }

    layer_descriptor = descriptor(layer_payload, "application/vnd.oci.image.layer.v1.tar")
    manifest_payload = json.dumps(
        {"schemaVersion": 2, "layers": [layer_descriptor]}, separators=(",", ":")
    ).encode()
    manifest_descriptor = descriptor(manifest_payload, "application/vnd.oci.image.manifest.v1+json")
    nested_payload = json.dumps(
        {"schemaVersion": 2, "manifests": [manifest_descriptor]}, separators=(",", ":")
    ).encode()
    nested_descriptor = descriptor(nested_payload, "application/vnd.oci.image.index.v1+json")
    nested_descriptor["annotations"] = {
        "io.containerd.image.name": f"docker.io/library/{image}",
        "org.opencontainers.image.ref.name": image.rsplit(":", maxsplit=1)[-1],
    }
    index_payload = json.dumps(
        {"schemaVersion": 2, "manifests": [nested_descriptor]}, separators=(",", ":")
    ).encode()

    blobs = (layer_payload, manifest_payload, nested_payload)
    with tarfile.open(archive, mode="w") as outer:
        member = tarfile.TarInfo("index.json")
        member.size = len(index_payload)
        outer.addfile(member, io.BytesIO(index_payload))
        for payload in blobs:
            digest = hashlib.sha256(payload).hexdigest()
            member = tarfile.TarInfo(f"blobs/sha256/{digest}")
            member.size = len(payload)
            outer.addfile(member, io.BytesIO(payload))


def test_server_compose_renderer_freezes_complete_no_build_stack(tmp_path: Path):
    output = tmp_path / "compose.server.yaml"
    result = subprocess.run(
        [
            sys.executable,
            str(RENDERER),
            "--source",
            str(REPO_ROOT / "compose.yaml"),
            "--output",
            str(output),
            "--backend-image",
            "dwg-agent-backend:release-test",
            "--frontend-image",
            "dwg-agent-frontend:release-test",
            "--mysql-image",
            "dwg-agent-mysql:release-test",
            "--minio-image",
            "dwg-agent-minio:release-test",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = yaml.safe_load(output.read_text(encoding="utf-8"))
    services = payload["services"]
    assert payload["name"] == "dwg-agent"
    assert len(services) == 16
    assert all("build" not in service for service in services.values())
    assert all("profiles" not in service for service in services.values())
    assert all(service["pull_policy"] == "never" for service in services.values())
    assert services["backend-api"]["image"] == "dwg-agent-backend:release-test"
    assert services["dispatcher"]["image"] == "dwg-agent-backend:release-test"
    assert services["worker-dxf-split"]["image"] == "dwg-agent-backend:release-test"
    assert services["nginx"]["image"] == "dwg-agent-frontend:release-test"
    assert services["mysql"]["image"] == "dwg-agent-mysql:release-test"
    assert services["minio"]["image"] == "dwg-agent-minio:release-test"


def test_release_scripts_encrypt_full_payload_and_never_ship_runtime_secrets():
    release = RELEASE_SCRIPT.read_text(encoding="utf-8")
    server = SERVER_SCRIPT.read_text(encoding="utf-8")
    verifier = ARCHIVE_VERIFIER.read_text(encoding="utf-8")

    assert "--recipient" in release
    assert "gpg --batch --yes --encrypt" in release
    assert "gpg --batch --list-secret-keys" in release
    assert "encrypted bundle decryption verification passed" in release
    assert 'gpg --batch --decrypt "$bundle"' in release
    assert "gzip -dc | tar -tf -" in release
    assert "docker image save" in release
    assert 'mysql_release="dwg-agent-mysql:${version}"' in release
    assert 'minio_release="dwg-agent-minio:${version}"' in release
    assert "--mysql-image" in release
    assert "--minio-image" in release
    assert "render_server_compose.py" in release
    assert "images.manifest" in release
    assert "business Python source remains" in release
    assert "load_verified_box_release_attestation" in release
    assert "get_excel_final_stage_root" in release
    assert "dwg_converter, dxf_converter" in release
    assert "check_dwg_environment().ok" in release
    assert "check_dxf_environment().ok" in release
    assert r"(\"config\", \"handbook\", \"material_routing\", \"pipeline\", \"main\")" in release
    assert "release_verify_oda_roundtrip" in release
    assert "scripts/release/fixtures/oda_runtime_smoke.dxf" in release
    assert "APPIMAGE runtime failed DWG to DXF" in release
    assert "APPIMAGE runtime failed DXF to DWG" in release
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=256m" in release
    assert "type=volume,destination=/app/var" in release
    assert "TMPDIR=/app/var/appimage-tmp" in release
    assert "--tmpfs /home/appuser:rw,nosuid,size=128m,uid=1000,gid=1000,mode=0700" in release
    assert 'python -m dxf2excel --help | grep -q "extract"' in release
    assert 'steel-dxf-classify --version | grep -q "steel-dxf-classifier"' in release
    assert "steel-dxf-split --help" in release
    assert "remnant-drawing-read --help" in release
    assert "material_routing" in release
    assert "remnant_drawing_reader" in release
    assert "__cpu_baseline__" in release
    assert r"{\"SSE\", \"SSE2\", \"SSE3\"}" in release
    assert r"f\"NumPy wheel requires an unsupported CPU baseline" in release
    assert "verify_image_archive.py" in release
    assert 'deploy.sh"' in release
    assert "--profile workers build" not in release
    assert "build backend-api nginx" in release
    assert "business Python source exists in an image layer" in verifier
    assert 'cp "$PROJECT_ROOT/.env.docker"' not in release
    assert ".env.docker.example" in release

    assert "gpg --batch --decrypt" in server
    assert "sha256sum -c SHA256SUMS" in server
    assert "docker image load" in server
    assert "docker image inspect" in server
    assert "--no-build" in server
    assert "CHANGE_ME_" in server
    assert "verify_live_remnant.py" in server
    assert "verify_runtime_features.py" in server
    assert "oda_runtime_smoke.dxf" in server


def test_server_recovery_starts_dependency_tiers_before_the_full_stack():
    server = SERVER_SCRIPT.read_text(encoding="utf-8")
    assert "exactly 16 services" in server

    recovery = server[server.index("server_recover()") : server.index("server_enable_service()")]
    storage_up = recovery.index('server_compose "$target" up -d --no-build mysql minio')
    storage_ready = recovery.index('server_wait_services "$target" 240 mysql minio')
    api_up = recovery.index('server_compose "$target" up -d --no-build backend-api')
    api_ready = recovery.index('server_wait_services "$target" 240 backend-api')
    full_up = recovery.index('server_compose "$target" up -d --no-build --remove-orphans')
    full_ready = recovery.index('server_wait_all_services "$target" 360')
    smoke = recovery.index('server_smoke "$target"')

    assert storage_up < storage_ready < api_up < api_ready < full_up < full_ready < smoke


def test_timezone_cutover_is_guarded_backed_up_and_reversible():
    source = TIMEZONE_SCRIPT.read_text(encoding="utf-8")

    for command in ("preflight", "migrate", "rollback"):
        assert f"server-timezone-migrate.sh {command}" in source
    for activity in (
        "workflow_input_dwg_folders.import",
        "workflow_input_excel.import",
        "file_transfers",
        "jobs",
        "job_dispatches",
        "upload_sessions",
    ):
        assert activity in source
    assert "docker info --format '{{.DockerRootDir}}'" in source
    assert "df -Pk" in source
    assert 'stop -t 180 nginx' in source
    assert "mysqldump" in source
    for option in ("--single-transaction", "--routines", "--triggers", "--events"):
        assert option in source
    assert "gzip -t" in source
    assert "sha256sum" in source
    assert "checksum=$(cd" not in source
    assert "sha256sum -c mysql-before.sql.gz.sha256 >/dev/null" in source
    assert "dwg_agent_timezone_verify_" in source
    for table in (
        "sys_users",
        "projects",
        "files",
        "file_transfers",
        "workflow_runs",
        "jobs",
        "audit_logs",
    ):
        assert table in source
    assert "a4c8e1f2b730" in source
    assert "d1e7f3a9c520" in source
    assert "alembic current" in source
    assert "alembic heads" in source
    assert "@@session.time_zone" in source
    assert "UTC_TIMESTAMP" in source
    assert 'mkdir -p -- "$target/backups"' in source
    assert "minio-before.jsonl" in source
    assert "timezone_verify_restored_counts" in source
    assert source.count("timezone_verify_restored_counts") >= 2
    assert "timezone_verify_migrated_counts" in source
    assert "file_bytes" in source
    assert "timezone_require_previous_runtime" in source
    assert "image_count == 4" in source
    for marker_key in (
        "counts_sha256",
        "minio_summary_sha256",
        "previous_compose_sha256",
        "previous_env_sha256",
        "previous_release_sha256",
        "previous_images_sha256",
    ):
        assert marker_key in source
    assert source.count('timezone_require_complete_inputs "$target"') >= 2
    assert source.count('timezone_require_pre_migration_head "$target"') >= 2
    assert source.count("/nginx-health") >= 2
    assert "timezone_acquire_lock" in source
    assert "flock -n" in source
    assert '"$backup/VERIFIED"' in source
    assert "VERIFIED_BACKUP_V1" in source
    assert "metadata query failed while checking table" in source
    assert "timezone_fail_closed" in source
    assert "TIMEZONE_MAINTENANCE_ACTIVE=1" in source
    assert "estimated * 2 / 1024" in source
    assert "baseline RELEASE:" in source
    assert "docker compose down -v" not in source
    assert "docker volume rm" not in source
    assert "DROP DATABASE $MYSQL_DATABASE" not in source

    syntax = subprocess.run(
        ["bash", "-n", str(TIMEZONE_SCRIPT)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_release_preserves_previous_runtime_and_packages_timezone_cutover():
    release = RELEASE_SCRIPT.read_text(encoding="utf-8")
    server = SERVER_SCRIPT.read_text(encoding="utf-8")

    assert "server-timezone-migrate.sh" in release
    assert '"$payload/scripts/server-timezone-migrate.sh"' in release
    assert ".rollback-candidate" in server
    assert 'chmod 0700 "$SERVER_ROLLBACK_TMP"' in server
    assert '.rollback-candidate.pending.XXXXXX' in server
    assert 'mv -T -- "$SERVER_ROLLBACK_TMP" "$rollback_candidate"' in server
    assert '[[ -e "$rollback_candidate" ]]' in server
    assert "server_acquire_maintenance_lock" in server
    assert '.timezone-migration.lock' in server
    assert 'install -m 0600 "$target/.env.docker"' in server
    assert '"$SERVER_TMP/scripts/server-timezone-migrate.sh"' in server
    assert '"$target/scripts/server-timezone-migrate.sh"' in server


def test_repeated_install_preserves_the_original_rollback_candidate(tmp_path: Path):
    target = tmp_path / "server"
    target.mkdir()
    installed = {
        "compose.server.yaml": "old-compose\n",
        ".env.docker": "MYSQL_DATABASE=dwg_agent\n",
        "RELEASE": "release-a\n",
        "images.manifest": "old-image\tsha256:a\n",
    }
    for name, content in installed.items():
        (target / name).write_text(content, encoding="utf-8")

    env = {**os.environ, "SERVER_SCRIPT": str(SERVER_SCRIPT), "TARGET": str(target)}
    command = 'source "$SERVER_SCRIPT" >/dev/null; server_preserve_rollback_candidate "$TARGET"'
    first = subprocess.run(
        ["bash", "-c", command], env=env, text=True, capture_output=True, check=False
    )
    assert first.returncode == 0, first.stderr

    (target / "RELEASE").write_text("release-b\n", encoding="utf-8")
    second = subprocess.run(
        ["bash", "-c", command], env=env, text=True, capture_output=True, check=False
    )
    assert second.returncode == 0, second.stderr
    assert (target / ".rollback-candidate" / "RELEASE").read_text() == "release-a\n"


def test_server_install_refuses_timezone_maintenance_lock(tmp_path: Path):
    lock_path = tmp_path / ".timezone-migration.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$SERVER_SCRIPT" >/dev/null; '
                'server_acquire_maintenance_lock "$TARGET"',
            ],
            env={
                **os.environ,
                "SERVER_SCRIPT": str(SERVER_SCRIPT),
                "TARGET": str(tmp_path),
            },
            text=True,
            capture_output=True,
            check=False,
        )
    assert result.returncode != 0
    assert "timezone maintenance is running" in result.stderr


def test_all_mutating_server_commands_share_the_timezone_lock():
    server = SERVER_SCRIPT.read_text(encoding="utf-8")
    boundaries = {
        "server_install()": "server_validate_runtime()",
        "server_recover()": "server_up()",
        "server_enable_service()": "server_status()",
        "server_smoke()": "server_down()",
        "server_down()": 'case "${1:-}"',
    }
    for start, end in boundaries.items():
        body = server[server.index(start) : server.index(end)]
        assert "server_acquire_maintenance_lock" in body
    enable = server[server.index("server_enable_service()") : server.index("server_status()")]
    assert enable.index("server_acquire_maintenance_lock") < enable.index("systemctl daemon-reload")
    assert enable.index("server_release_maintenance_lock") < enable.index("systemctl start")


def test_timezone_table_probe_fails_closed_on_database_error(tmp_path: Path):
    target = tmp_path / "server"
    target.mkdir()
    (target / ".env.docker").write_text("MYSQL_DATABASE=dwg_agent\n", encoding="utf-8")
    env = {**os.environ, "TIMEZONE_SCRIPT": str(TIMEZONE_SCRIPT), "TARGET": str(target)}
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$TIMEZONE_SCRIPT" >/dev/null; '
            "timezone_mysql_scalar() { return 23; }; "
            'timezone_table_exists "$TARGET" jobs',
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "metadata query failed while checking table: jobs" in result.stderr


def test_unverified_timezone_backup_is_rejected(tmp_path: Path):
    target = tmp_path / "server"
    backup = target / "backups" / "timezone-20260801-120000"
    backup.mkdir(parents=True)
    (target / ".env.docker").write_text("MYSQL_DATABASE=dwg_agent\n", encoding="utf-8")
    (backup / "mysql-before.sql.gz").write_bytes(b"incomplete")
    env = {
        **os.environ,
        "TIMEZONE_SCRIPT": str(TIMEZONE_SCRIPT),
        "TARGET": str(target),
        "BACKUP": str(backup),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$TIMEZONE_SCRIPT" >/dev/null; '
            'timezone_require_verified_backup "$TARGET" "$BACKUP"',
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "backup has no verified-complete marker" in result.stderr


def test_timezone_maintenance_failure_stops_gateway_and_writers(tmp_path: Path):
    call_log = tmp_path / "compose-calls"
    env = {
        **os.environ,
        "TIMEZONE_SCRIPT": str(TIMEZONE_SCRIPT),
        "TARGET": str(tmp_path),
        "CALL_LOG": str(call_log),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$TIMEZONE_SCRIPT" >/dev/null; '
            "timezone_compose() { local target=$1; shift; "
            "if [[ $1 == config ]]; then printf '%s\\n' backend-api nginx; "
            'else printf \'%s\\n\' "$*" >>"$CALL_LOG"; fi; }; '
            'TIMEZONE_TARGET="$TARGET"; TIMEZONE_MAINTENANCE_ACTIVE=1; false',
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "maintenance failed closed" in result.stderr
    assert "stop -t 60 backend-api nginx" in call_log.read_text(encoding="utf-8")


def test_timezone_count_manifest_requires_every_protected_measure(tmp_path: Path):
    target = tmp_path / "server"
    backup = target / "backups" / "timezone-20260801-120000"
    backup.mkdir(parents=True)
    (target / ".env.docker").write_text("MYSQL_DATABASE=dwg_agent\n", encoding="utf-8")
    (backup / "pre-migration-counts.tsv").write_text(
        "source_database\tdwg_agent\nalembic_head\td1e7f3a9c520\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "TIMEZONE_SCRIPT": str(TIMEZONE_SCRIPT),
        "TARGET": str(target),
        "BACKUP": str(backup),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$TIMEZONE_SCRIPT" >/dev/null; '
            'timezone_verify_preserved_counts "$TARGET" "$BACKUP" test',
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "manifest is missing required key: sys_users" in result.stderr


def test_timezone_cutover_verifies_database_before_async_writers_and_drains_rollback():
    source = TIMEZONE_SCRIPT.read_text(encoding="utf-8")
    migrate = source[
        source.index("timezone_migrate()") : source.index("timezone_restore_database()")
    ]
    rollback = source[source.index("timezone_rollback()") :]

    api_ready = migrate.index('timezone_wait_services "$target" 300 backend-api')
    database_verified = migrate.index('timezone_verify_database_runtime "$target"')
    counts_verified = migrate.index('timezone_verify_migrated_counts "$target" "$backup"')
    dispatcher_start = migrate.index('force-recreate dispatcher')
    workers_start = migrate.index('force-recreate "${workers[@]}"')
    assert api_ready < database_verified < counts_verified < dispatcher_start < workers_start
    backup_created = migrate.index('timezone_create_backup "$target" "$backup"')
    rollback_runtime_ready = migrate.index('timezone_require_previous_runtime "$target" "$backup"')
    new_mysql_start = migrate.index('force-recreate mysql minio')
    assert backup_created < rollback_runtime_ready < new_mysql_start

    gateway_stop = rollback.index('stop -t 180 nginx')
    quiescent = rollback.index('timezone_require_quiescent "$target"')
    application_stop = rollback.index('stop -t 180 "${services[@]}"')
    assert gateway_stop < quiescent < application_stop
    assert 'dirname -- "$backup"' in rollback
    assert "^timezone-[0-9]{8}-[0-9]{6}$" in rollback
    assert 'timezone_require_verified_backup "$target" "$backup"' in rollback
    assert "('open','uploading','ready','finalizing')" in source
    assert '[[ "$requested_backup" == /* ]]' in rollback
    minio_verified = rollback.index('timezone_verify_minio_unchanged "$target" "$backup"')
    database_restore = rollback.index('timezone_restore_database "$target" "$backup"')
    assert minio_verified < database_restore
    old_api_ready = rollback.index('timezone_wait_services "$target" 300 backend-api')
    old_runtime_verified = rollback.index('timezone_verify_rollback_database_runtime "$target"')
    workers_start = rollback.index('force-recreate "${workers[@]}"')
    assert database_restore < old_api_ready < old_runtime_verified < workers_start


def test_server_release_gates_env_and_runtime_feature_matrix_before_remnant_smoke():
    server = SERVER_SCRIPT.read_text(encoding="utf-8")
    validation = server[
        server.index("server_validate_runtime()") : server.index("server_recover()")
    ]
    smoke = server[server.index("server_smoke()") : server.index("server_down()")]

    for key in (
        "DXF_PIPELINE_ENABLED",
        "DXF2DWG_PIPELINE_ENABLED",
        "DXF2EXCEL_PIPELINE_ENABLED",
        "DXF_CLASSIFICATION_PIPELINE_ENABLED",
        "DXF_SPLIT_PIPELINE_ENABLED",
        "EXCEL_FINAL_PIPELINE_ENABLED",
        "REMNANT_INVENTORY_ENABLED",
    ):
        assert key in validation
    runtime_features = smoke.index("verify_runtime_features.py")
    remnant_round_trip = smoke.index("verify_live_remnant.py")
    assert runtime_features < remnant_round_trip


def test_runtime_feature_verifier_has_exact_public_contract():
    source = RUNTIME_FEATURE_VERIFIER.read_text(encoding="utf-8")

    for setting in (
        "dxf_pipeline_enabled",
        "dxf2dwg_pipeline_enabled",
        "dxf2excel_pipeline_enabled",
        "dxf_classification_pipeline_enabled",
        "dxf_split_pipeline_enabled",
        "excel_final_pipeline_enabled",
        "remnant_inventory_enabled",
    ):
        assert setting in source
    assert "model_dump" not in source
    assert "os.environ" not in source
    assert 'PIPELINE_QUEUE_MAP.get("excel_stage2")' in source
    assert 'WORKFLOW_TEMPLATES["linux_production"]' in source


def _run_runtime_feature_verifier(**overrides: str) -> subprocess.CompletedProcess[str]:
    feature_env = {
        "APP_ENV": "development",
        "MYSQL_PASSWORD": "mysql-runtime-secret",
        "JWT_SECRET_KEY": "jwt-runtime-secret",
        "DXF_PIPELINE_ENABLED": "true",
        "DXF2DWG_PIPELINE_ENABLED": "true",
        "DXF2EXCEL_PIPELINE_ENABLED": "false",
        "DXF_CLASSIFICATION_PIPELINE_ENABLED": "true",
        "DXF_SPLIT_PIPELINE_ENABLED": "true",
        "EXCEL_FINAL_PIPELINE_ENABLED": "true",
        "REMNANT_INVENTORY_ENABLED": "true",
        **overrides,
    }
    return subprocess.run(
        [sys.executable, str(RUNTIME_FEATURE_VERIFIER)],
        cwd=REPO_ROOT / "backend",
        env={**os.environ, **feature_env},
        text=True,
        capture_output=True,
        check=False,
    )


def test_runtime_feature_verifier_accepts_only_the_approved_matrix():
    accepted = _run_runtime_feature_verifier()
    rejected = _run_runtime_feature_verifier(REMNANT_INVENTORY_ENABLED="false")

    assert accepted.returncode == 0, accepted.stderr
    payload = json.loads(accepted.stdout)
    assert payload["status"] == "ok"
    assert payload["always_on_capabilities"] == {"excel_stage2": True}
    assert rejected.returncode == 1
    assert "remnant_inventory_enabled" in rejected.stderr
    for secret in ("mysql-runtime-secret", "jwt-runtime-secret"):
        assert secret not in accepted.stdout
        assert secret not in accepted.stderr
        assert secret not in rejected.stdout
        assert secret not in rejected.stderr


def test_server_systemd_service_runs_recovery_after_docker_and_retries_failures():
    server = SERVER_SCRIPT.read_text(encoding="utf-8")

    assert "server-deploy.sh enable-service TARGET_DIR" in server
    assert "Requires=docker.service" in server
    assert "After=docker.service network-online.target" in server
    assert "Wants=network-online.target" in server
    assert "Restart=on-failure" in server
    assert "RestartSec=15s" in server
    assert "ExecStart=$target/scripts/server-deploy.sh recover $target" in server
    assert "ExecReload=$target/scripts/server-deploy.sh recover $target" in server
    assert "ExecStop=$target/scripts/server-deploy.sh down $target" in server
    assert "systemctl enable dwg-agent.service" in server
    assert "systemctl start dwg-agent.service" in server


def test_server_release_checks_docker_root_space_before_install_and_recovery():
    server = SERVER_SCRIPT.read_text(encoding="utf-8")

    assert "server_require_docker_disk_space()" in server
    install = server[server.index("server_install()") : server.index("server_wait_services()")]
    validate = server[
        server.index("server_validate_runtime()") : server.index("server_recover()")
    ]
    assert install.index("server_require_docker_disk_space") < install.index(
        "docker image load"
    )
    assert "server_require_docker_disk_space" in validate


def test_backend_numpy_stays_compatible_with_baseline_x86_64_servers():
    pyproject = PYPROJECT.read_text(encoding="utf-8")

    assert '"numpy>=1.24,<2.4"' in pyproject


def test_oda_release_smoke_fixture_is_portable_and_nonempty():
    payload = ODA_SMOKE_FIXTURE.read_bytes()

    assert len(payload) > 1024
    assert b"$ACADVER" in payload
    assert payload.rstrip().endswith(b"EOF")


def test_live_remnant_verifier_is_explicit_and_self_cleaning():
    source = LIVE_REMNANT_VERIFIER.read_text(encoding="utf-8")

    assert "--fixture" in source
    assert "save_bytes_as_file" in source
    assert "run_parse_item" in source
    assert "confirm_import_items" in source
    assert "find_available_remnants" in source
    assert "remnant_file_access_decision" in source
    assert "stat_object" in source
    assert "iter_file" in source
    assert "cleanup" in source
    assert "delete_object" in source

    result = subprocess.run(
        [sys.executable, str(LIVE_REMNANT_VERIFIER), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "真实 MySQL/MinIO" in result.stdout


def test_protected_runtime_and_context_exclude_business_source_and_samples():
    dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert "FROM runtime-base AS protected" in dockerfile
    assert "FROM runtime AS bytecode-compiler" in dockerfile
    assert "COPY --from=bytecode-compiler /app/app /app/app" in dockerfile
    protected_section = dockerfile.split("FROM runtime-base AS protected", maxsplit=1)[1]
    assert "COPY backend/app" not in protected_section
    assert "COPY Stages/" not in protected_section
    assert "python -m compileall" in dockerfile
    assert "write_protected_runtime_manifest" in dockerfile
    assert "chmod 0444" in dockerfile
    assert "-type f -name '*.py' -delete" in dockerfile
    assert "Stages/*/data/" in dockerignore
    assert "Stages/*/tests/" in dockerignore
    assert "Stages/dxf2excel/convert/" in dockerignore
    assert "Stages/dxf2dwg/convert/" in dockerignore
    assert "*.pdf" in dockerignore
    assert "-name '*.egg-info'" in dockerfile
    assert "/app/Stages/dwg2dxf/tools" in dockerfile
    assert (
        "COPY scripts/release/verify_live_remnant.py /app/scripts/release/verify_live_remnant.py"
    ) in dockerfile
    assert (
        "COPY scripts/release/verify_runtime_features.py "
        "/app/scripts/release/verify_runtime_features.py"
    ) in dockerfile
    assert (
        "COPY scripts/release/fixtures/oda_runtime_smoke.dxf "
        "/app/scripts/release/fixtures/oda_runtime_smoke.dxf"
    ) in dockerfile
    assert "-name 'Makefile'" in dockerfile
    assert compose["x-app-image"]["build"]["target"] == "protected"
    assert compose["x-app-service"]["read_only"] is True
    assert compose["x-app-service"]["cap_drop"] == ["ALL"]


def test_backend_initializes_sql_broker_before_becoming_healthy():
    dockerfile = (REPO_ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    command = dockerfile.split('CMD ["sh", "-c",', maxsplit=1)[1]

    wait_at = command.index("python -m app.platform.database.wait")
    migrate_at = command.index("alembic upgrade head")
    seed_at = command.index("python -m app.bootstrap.seed")
    broker_at = command.index("python -m app.platform.messaging.prepare")
    serve_at = command.index("exec gunicorn")

    assert wait_at < migrate_at < seed_at < broker_at < serve_at


def test_image_archive_verifier_rejects_source_hidden_in_old_layer(tmp_path: Path):
    archive = tmp_path / "images.tar"
    image = "dwg-agent-backend:layer-test"
    _write_legacy_image_archive(
        archive,
        image=image,
        layer_members=("app/app/main.py", "app/app/main.pyc"),
    )

    result = subprocess.run(
        [sys.executable, str(ARCHIVE_VERIFIER), "--archive", str(archive), "--image", image],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "app/app/main.py" in result.stderr


def test_image_archive_verifier_accepts_sourceless_business_layers(tmp_path: Path):
    archive = tmp_path / "images.tar"
    image = "dwg-agent-backend:layer-test"
    _write_legacy_image_archive(
        archive,
        image=image,
        layer_members=("app/app/main.pyc", "app/Stages/excel_final/main.pyc"),
    )

    result = subprocess.run(
        [sys.executable, str(ARCHIVE_VERIFIER), "--archive", str(archive), "--image", image],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_image_archive_verifier_accepts_docker_normalized_nested_oci_tag(tmp_path: Path):
    archive = tmp_path / "images.tar"
    image = "dwg-agent-backend:layer-test"
    _write_nested_oci_image_archive(
        archive,
        image=image,
        member_name="app/app/main.pyc",
    )

    result = subprocess.run(
        [sys.executable, str(ARCHIVE_VERIFIER), "--archive", str(archive), "--image", image],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
