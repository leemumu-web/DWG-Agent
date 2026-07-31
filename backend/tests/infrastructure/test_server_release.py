from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import yaml

from tests.support.paths import REPO_ROOT

RENDERER = REPO_ROOT / "scripts/release/render_server_compose.py"
RELEASE_SCRIPT = REPO_ROOT / "scripts/release.sh"
SERVER_SCRIPT = REPO_ROOT / "scripts/release/server-deploy.sh"
ARCHIVE_VERIFIER = REPO_ROOT / "scripts/release/verify_image_archive.py"
LIVE_REMNANT_VERIFIER = REPO_ROOT / "scripts/release/verify_live_remnant.py"
RUNTIME_FEATURE_VERIFIER = REPO_ROOT / "scripts/release/verify_runtime_features.py"
ODA_SMOKE_FIXTURE = REPO_ROOT / "scripts/release/fixtures/oda_runtime_smoke.dxf"
PYPROJECT = REPO_ROOT / "backend" / "pyproject.toml"


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
    assert len(services) == 15
    assert all("build" not in service for service in services.values())
    assert all("profiles" not in service for service in services.values())
    assert all(service["pull_policy"] == "never" for service in services.values())
    assert services["backend-api"]["image"] == "dwg-agent-backend:release-test"
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
    assert "exactly 15 services" in server

    recovery = server[server.index("server_recover()") : server.index("server_enable_service()")]
    storage_up = recovery.index('server_compose "$target" up -d --no-build mysql minio')
    storage_ready = recovery.index('server_wait_services "$target" 240 mysql minio')
    api_up = recovery.index('server_compose "$target" up -d --no-build backend-api')
    api_ready = recovery.index('server_wait_services "$target" 240 backend-api')
    full_up = recovery.index('server_compose "$target" up -d --no-build --remove-orphans')
    full_ready = recovery.index('server_wait_all_services "$target" 360')
    smoke = recovery.index('server_smoke "$target"')

    assert storage_up < storage_ready < api_up < api_ready < full_up < full_ready < smoke


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
        "EXCEL_STAGE2_PIPELINE_ENABLED",
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
        "excel_stage2_pipeline_enabled",
        "remnant_inventory_enabled",
    ):
        assert setting in source
    assert "model_dump" not in source
    assert "os.environ" not in source


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
    assert "systemctl enable --now dwg-agent.service" in server


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
