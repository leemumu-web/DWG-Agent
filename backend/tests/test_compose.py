"""Verify compose.yaml is valid YAML and has expected defensive defaults.

These are lightweight static checks — no Docker daemon required.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
COMPOSE_PATH = REPO_ROOT / "compose.yaml"
DEV_COMPOSE_PATH = REPO_ROOT / "compose.dev.yaml"
DOCKERFILE_PATH = REPO_ROOT / "backend" / "Dockerfile"
DOCKERIGNORE_PATH = REPO_ROOT / ".dockerignore"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
DOCKER_ENV_EXAMPLE_PATH = REPO_ROOT / ".env.docker.example"
APP_SECRET_KEYS = {
    "JWT_SECRET_KEY",
    "SUPER_ADMIN_PASSWORD",
    "DATABASE_URL",
}
APP_SERVICE_NAMES = (
    "backend-api",
    "worker-agent",
    "worker-dxf",
    "worker-dxf2dwg",
    "worker-dxf2excel",
    "worker-excel-final",
    "worker-report",
)


def _load():
    with open(COMPOSE_PATH) as f:
        return yaml.safe_load(f)


def _load_dev():
    with open(DEV_COMPOSE_PATH) as f:
        return yaml.safe_load(f)


def _assert_blank_environment(service: dict, keys: set[str]) -> None:
    env = service.get("environment", {})
    for key in keys:
        assert env.get(key) == "", f"{key} should be scrubbed from {service}"


class TestAppServices:
    def test_app_services_use_docker_env_file_and_hide_root_passwords(self):
        data = _load()
        for name in APP_SERVICE_NAMES:
            service = data["services"][name]
            assert service["env_file"] == [".env.docker"]
            assert service["environment"]["MYSQL_ROOT_PASSWORD"] == ""
            assert service["environment"]["MINIO_ROOT_PASSWORD"] == ""

    def test_worker_services_use_process_healthchecks_without_remote_control(self):
        data = _load()
        for service_name in APP_SERVICE_NAMES[1:]:
            command = " ".join(data["services"][service_name]["healthcheck"]["test"])
            assert "/tmp/dwg-celery-ready" in command
            assert "inspect" not in command
            assert "localhost:8010/health" not in command
            if service_name in {"worker-dxf", "worker-dxf2dwg"}:
                expected_queue = "dxf2dwg" if service_name.endswith("dxf2dwg") else "dxf"
                assert f"/tmp/dwg-celery-{expected_queue}.pid" in command
                assert "kill -0" in command
            else:
                assert "/proc/1/cmdline" in command

    def test_unsupported_flower_service_is_absent(self):
        data = _load()
        assert "flower" not in data["services"]

    def test_stage_dependencies_and_standalone_excel_runner_are_copied_into_image(self):
        dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")

        for stage in ("dwg2dxf", "dxf2dwg", "dxf2excel"):
            assert f"COPY Stages/{stage} ./Stages/{stage}" in dockerfile
        assert "COPY Stages/excel_final /app/Stages/excel_final" in dockerfile
        assert "COPY scripts/run-cad-worker.sh /app/scripts/run-cad-worker.sh" in dockerfile

    def test_conversion_workers_use_persistent_xvfb_and_configurable_concurrency(self):
        data = _load()
        expected = {
            "worker-dxf": ("dxf", "${DXF_WORKER_CONCURRENCY:-8}", "${DXF_WORKER_DISPLAY:-:91}"),
            "worker-dxf2dwg": (
                "dxf2dwg",
                "${DXF2DWG_WORKER_CONCURRENCY:-8}",
                "${DXF2DWG_WORKER_DISPLAY:-:92}",
            ),
        }

        for service_name, (queue, concurrency, display) in expected.items():
            service = data["services"][service_name]
            command = service["command"]
            assert command[0] == "/app/scripts/run-cad-worker.sh"
            assert command[1] == queue
            assert command[2] == concurrency
            assert command[4] == display
            health = " ".join(service["healthcheck"]["test"])
            assert f"/tmp/dwg-celery-{queue}.pid" in health


class TestComposeYamlValid:
    def test_is_parseable_yaml(self):
        assert _load() is not None

    def test_has_expected_services(self):
        data = _load()
        actual = set(data.get("services", {}))
        required = {
            "nginx",
            "backend-api",
            "worker-agent",
            "worker-dxf",
            "worker-dxf2dwg",
            "worker-dxf2excel",
            "worker-excel-final",
            "worker-report",
            "mysql",
            "minio",
        }
        assert required <= actual, f"Missing services: {sorted(required - actual)}"

    def test_core_infra_images_do_not_depend_on_docker_hub(self):
        data = _load()
        services = data["services"]

        assert services["nginx"]["build"]["dockerfile"] == "frontend/Dockerfile"
        assert "dwg-agent-frontend:local" in services["nginx"]["image"]
        assert "mysql/community-server:8.4" in services["mysql"]["image"]
        assert "quay.io/minio/minio@sha256:" in services["minio"]["image"]
        assert ":latest" not in services["minio"]["image"]
        assert "${HTTP_PORT:-80}:8080" in services["nginx"]["ports"]

        nginx_conf = (REPO_ROOT / "infra/gateway/nginx/nginx.conf").read_text()
        assert "listen 8080;" in nginx_conf
        assert "listen 80;" not in nginx_conf

    def test_docker_nginx_conf_uses_unprivileged_runtime_paths(self):
        nginx_conf = (REPO_ROOT / "infra/gateway/nginx/nginx.conf").read_text()

        assert "/var/log/nginx" not in nginx_conf
        assert "error_log /dev/stderr warn;" in nginx_conf
        assert "access_log /dev/stdout extended;" in nginx_conf
        assert "pid /tmp/nginx.pid;" in nginx_conf
        for temp_path in (
            "proxy_temp_path /tmp/proxy_temp;",
            "client_body_temp_path /tmp/client_temp;",
            "fastcgi_temp_path /tmp/fastcgi_temp;",
            "uwsgi_temp_path /tmp/uwsgi_temp;",
            "scgi_temp_path /tmp/scgi_temp;",
        ):
            assert temp_path in nginx_conf


class TestDevelopmentCompose:
    def test_dev_override_is_parseable_and_uses_current_backend_port(self):
        data = _load_dev()
        backend = data["services"]["backend-api"]

        assert "uvicorn app.main:app" in backend["command"]
        assert "--reload" in backend["command"]
        assert "--port 8010" in backend["command"]
        assert backend["ports"] == ["127.0.0.1:8010:8010"]

    def test_dev_override_mounts_backend_source_into_every_implemented_worker(self):
        data = _load_dev()
        workers = (
            "worker-report",
            "worker-dxf",
            "worker-dxf2dwg",
            "worker-dxf2excel",
            "worker-excel-final",
        )
        for worker in workers:
            volumes = data["services"][worker]["volumes"]
            assert "./backend/app:/app/app" in volumes

        assert "./Stages/dwg2dxf:/app/Stages/dwg2dxf" in data["services"]["worker-dxf"]["volumes"]
        assert "./Stages/dxf2dwg:/app/Stages/dxf2dwg" in data["services"]["worker-dxf2dwg"]["volumes"]
        assert "./Stages/dxf2excel:/app/Stages/dxf2excel" in data["services"]["worker-dxf2excel"]["volumes"]
        assert "./Stages/excel_final:/app/Stages/excel_final" in data["services"]["worker-excel-final"]["volumes"]

    def test_dev_override_does_not_publish_mysql_or_minio(self):
        services = _load_dev()["services"]

        assert "mysql" not in services or "ports" not in services["mysql"]
        assert "minio" not in services or "ports" not in services["minio"]


class TestMysqlService:
    def test_mysql_uses_docker_env_file_and_scrubs_app_secrets(self):
        data = _load()
        mysql = data["services"]["mysql"]
        assert mysql["env_file"] == [".env.docker"]
        _assert_blank_environment(
            mysql,
            APP_SECRET_KEYS
            | {"MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"},
        )
        assert "MYSQL_PASSWORD" not in mysql["environment"]
        assert "MYSQL_ROOT_PASSWORD" not in mysql["environment"]

    def test_mysql_volumes_include_init_sql(self):
        data = _load()
        volumes = data["services"]["mysql"]["volumes"]
        init_mounts = [v for v in volumes if "init.sql" in str(v)]
        assert len(init_mounts) >= 1, "init.sql should be mounted"

    def test_mysql_initializes_hardware_handbook_after_platform_grants(self):
        data = _load()
        volumes = data["services"]["mysql"]["volumes"]

        assert any("01-platform.sql" in str(volume) for volume in volumes)
        assert any("02-hardware-handbook.sql" in str(volume) for volume in volumes)
        init_sql = (REPO_ROOT / "infra/database/mysql/init.sql").read_text(
            encoding="utf-8"
        )
        assert "GRANT SELECT ON hardware_handbook.*" in init_sql

    def test_mysql_has_healthcheck(self):
        data = _load()
        hc = data["services"]["mysql"]["healthcheck"]
        test_cmd = " ".join(hc["test"])
        assert "mysqladmin" in test_cmd
        assert "$${MYSQL_ROOT_PASSWORD}" in test_cmd
        assert "${MYSQL_ROOT_PASSWORD:-" not in test_cmd


class TestMinioService:
    def test_minio_uses_docker_env_file_and_scrubs_unrelated_secrets(self):
        data = _load()
        minio = data["services"]["minio"]
        assert minio["env_file"] == [".env.docker"]
        _assert_blank_environment(
            minio,
            APP_SECRET_KEYS
            | {
                "MYSQL_PASSWORD",
                "MYSQL_ROOT_PASSWORD",
                "MINIO_ACCESS_KEY",
                "MINIO_SECRET_KEY",
            },
        )
        assert "MINIO_ROOT_PASSWORD" not in minio["environment"]


class TestClassifiedInfrastructureLayout:
    def test_runtime_assets_live_under_explicit_owners(self):
        for relative in (
            "infra/gateway/nginx/nginx.conf",
            "infra/database/mysql/init.sql",
            "infra/storage/minio",
            "infra/verification/verify.sh",
        ):
            assert (REPO_ROOT / relative).exists(), relative

    def test_rabbitmq_target_is_truthful_and_not_silently_deployed(self):
        data = _load()
        readme = (REPO_ROOT / "infra/messaging/rabbitmq/README.md").read_text()

        assert "rabbitmq" not in data["services"]
        assert "Status: target contract, not deployed in current Compose." in readme
        assert "MySQL SQLAlchemy Celery transport" in readme

    def test_windows_boundary_is_split_by_process_role(self):
        for relative in (
            "windows/node-agent/README.md",
            "windows/cam-runner/README.md",
            "windows/sinocam-adapter/README.md",
            "windows/protocols/README.md",
        ):
            assert (REPO_ROOT / relative).is_file(), relative

        assert not (REPO_ROOT / "cad-worker").exists()

    def test_root_logo_duplicate_is_removed(self):
        assert (REPO_ROOT / "frontend/public/logo.png").is_file()
        assert not (REPO_ROOT / "image.png").exists()


class TestDockerEnvironmentFiles:
    def _env_keys(self, path: Path) -> set[str]:
        return {
            line.split("=", 1)[0]
            for line in path.read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
        }

    def test_docker_env_example_exists(self):
        assert DOCKER_ENV_EXAMPLE_PATH.exists(), ".env.docker.example must be committed"

    def test_env_examples_have_same_keys(self):
        local_keys = self._env_keys(REPO_ROOT / ".env.example")
        docker_keys = self._env_keys(DOCKER_ENV_EXAMPLE_PATH)
        assert docker_keys == local_keys

    def test_docker_env_example_has_no_nested_compose_interpolation(self):
        content = DOCKER_ENV_EXAMPLE_PATH.read_text()
        assert "${" not in content, (
            "env_file values must not depend on shell/root .env interpolation"
        )
        assert "mysql:3306" in content
        assert "http://minio:9000" in content

    def test_local_docker_env_is_gitignored(self):
        content = GITIGNORE_PATH.read_text()
        assert ".env.docker" in content


# ── Dockerfile static checks ──────────────────────────────────────


class TestDockerfile:
    def test_exists(self):
        assert DOCKERFILE_PATH.exists(), "Dockerfile missing"

    def test_is_multi_stage(self):
        content = DOCKERFILE_PATH.read_text()
        stages = [line for line in content.splitlines() if line.startswith("FROM ")]
        assert len(stages) >= 2, "Dockerfile should use multi-stage build"

    def test_uses_buildable_python_uv_base_image(self):
        content = DOCKERFILE_PATH.read_text()

        assert "FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder" in content
        assert "FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime" in content
        assert "FROM python:3.12-slim" not in content

    def test_does_not_copy_uv_from_latest_image(self):
        content = DOCKERFILE_PATH.read_text()

        assert "ghcr.io/astral-sh/uv:latest" not in content

    def test_copies_packaging_readme_before_uv_sync(self):
        content = DOCKERFILE_PATH.read_text()

        assert "COPY backend/pyproject.toml backend/uv.lock backend/README.md ./backend/" in content

    def test_has_non_root_user(self):
        content = DOCKERFILE_PATH.read_text()
        assert "USER appuser" in content, "Must run as non-root user (spec §17.5-4)"
        assert "useradd" in content or "adduser" in content, "Must create appuser"
        assert "ENV HOME=/home/appuser" in content
        assert "mkdir -p /app/var /home/appuser" in content

    def test_runtime_runs_alembic_before_gunicorn(self):
        content = DOCKERFILE_PATH.read_text()
        assert content.index("alembic upgrade head") < content.index("python -m app.db.init_db")
        assert content.index("python -m app.db.init_db") < content.index("exec gunicorn")

    def test_has_healthcheck(self):
        content = DOCKERFILE_PATH.read_text()
        assert "HEALTHCHECK" in content, "Dockerfile should have HEALTHCHECK"

    def test_does_not_copy_env_file(self):
        """Spec §17.5-3: .env must not be baked into image."""
        content = DOCKERFILE_PATH.read_text()
        assert ".env" not in content, "Must not COPY .env into image"


class TestDockerignore:
    def test_exists(self):
        assert DOCKERIGNORE_PATH.exists(), ".dockerignore missing"

    def test_excludes_tests(self):
        content = DOCKERIGNORE_PATH.read_text()
        assert "tests/" in content, ".dockerignore should exclude tests/"

    def test_excludes_env(self):
        content = DOCKERIGNORE_PATH.read_text()
        assert ".env" in content, ".dockerignore should exclude .env"

    def test_excludes_venv_and_cache(self):
        content = DOCKERIGNORE_PATH.read_text()
        assert ".venv/" in content
        assert "__pycache__" in content

    def test_excludes_large_assets_not_used_by_backend_image(self):
        content = DOCKERIGNORE_PATH.read_text()
        for path in (
            "third_parts/",
            "Stages/dxf2excel/original_dxf/",
            "Stages/dwg2dxf/convert/",
            "Stages/dxf2dwg/tools/oda/",
        ):
            assert path in content
