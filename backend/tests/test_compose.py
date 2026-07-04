"""Verify compose.yaml is valid YAML and has expected defensive defaults.

These are lightweight static checks — no Docker daemon required.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
COMPOSE_PATH = REPO_ROOT / "compose.yaml"
DOCKERFILE_PATH = REPO_ROOT / "backend" / "Dockerfile"
DOCKERIGNORE_PATH = REPO_ROOT / "backend" / ".dockerignore"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
DOCKER_ENV_EXAMPLE_PATH = REPO_ROOT / ".env.docker.example"
APP_SECRET_KEYS = {
    "JWT_SECRET_KEY",
    "SUPER_ADMIN_PASSWORD",
    "DATABASE_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
}
APP_SERVICE_NAMES = ("backend-api", "worker-agent", "worker-dxf", "worker-report", "flower")


def _load():
    with open(COMPOSE_PATH) as f:
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

    def test_worker_services_override_api_healthcheck_with_celery_ping(self):
        data = _load()
        expected_nodes = {
            "worker-agent": "agent@$$HOSTNAME",
            "worker-dxf": "dxf@$$HOSTNAME",
            "worker-report": "report@$$HOSTNAME",
        }

        for service_name, node_name in expected_nodes.items():
            command = " ".join(data["services"][service_name]["healthcheck"]["test"])
            assert "/app/.venv/bin/celery" in command
            assert "inspect ping" in command
            assert node_name in command
            assert "localhost:8000/health" not in command

    def test_flower_overrides_api_healthcheck_with_flower_http_probe(self):
        data = _load()

        command = " ".join(data["services"]["flower"]["healthcheck"]["test"])
        assert "localhost:5555" in command
        assert "localhost:8000/health" not in command


class TestComposeYamlValid:
    def test_is_parseable_yaml(self):
        assert _load() is not None

    def test_has_expected_services(self):
        data = _load()
        services = data.get("services", {})
        for name in ("nginx", "backend-api", "mysql", "redis", "minio"):
            assert name in services, f"Missing service: {name}"

    def test_core_infra_images_do_not_depend_on_docker_hub(self):
        data = _load()
        services = data["services"]

        assert services["nginx"]["image"] == "ghcr.io/nginxinc/nginx-unprivileged:1.27-alpine"
        assert services["mysql"]["image"] == (
            "container-registry.oracle.com/mysql/community-server:8.4"
        )
        assert services["redis"]["image"] == "ghcr.io/valkey-io/valkey:9.0-alpine"
        assert services["minio"]["image"] == "quay.io/minio/minio:latest"
        assert "80:8080" in services["nginx"]["ports"]

        nginx_conf = (REPO_ROOT / "infra/nginx/nginx.conf").read_text()
        assert "listen 8080;" in nginx_conf
        assert "listen 80;" not in nginx_conf

    def test_docker_nginx_conf_uses_unprivileged_runtime_paths(self):
        nginx_conf = (REPO_ROOT / "infra/nginx/nginx.conf").read_text()

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


class TestMysqlService:
    def test_mysql_uses_docker_env_file_and_scrubs_app_secrets(self):
        data = _load()
        mysql = data["services"]["mysql"]
        assert mysql["env_file"] == [".env.docker"]
        _assert_blank_environment(
            mysql,
            APP_SECRET_KEYS
            | {"REDIS_PASSWORD", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"},
        )
        assert "MYSQL_PASSWORD" not in mysql["environment"]
        assert "MYSQL_ROOT_PASSWORD" not in mysql["environment"]

    def test_mysql_volumes_include_init_sql(self):
        data = _load()
        volumes = data["services"]["mysql"]["volumes"]
        init_mounts = [v for v in volumes if "init.sql" in str(v)]
        assert len(init_mounts) >= 1, "init.sql should be mounted"

    def test_mysql_has_healthcheck(self):
        data = _load()
        hc = data["services"]["mysql"]["healthcheck"]
        test_cmd = " ".join(hc["test"])
        assert "mysqladmin" in test_cmd
        assert "$${MYSQL_ROOT_PASSWORD}" in test_cmd
        assert "${MYSQL_ROOT_PASSWORD:-" not in test_cmd


class TestRedisService:
    def test_redis_uses_docker_env_file_without_root_env_interpolation(self):
        data = _load()
        redis = data["services"]["redis"]
        assert redis["env_file"] == [".env.docker"]
        _assert_blank_environment(
            redis,
            APP_SECRET_KEYS
            | {
                "MYSQL_PASSWORD",
                "MYSQL_ROOT_PASSWORD",
                "MINIO_ACCESS_KEY",
                "MINIO_SECRET_KEY",
                "MINIO_ROOT_PASSWORD",
            },
        )
        assert "REDIS_PASSWORD" not in redis["environment"]
        cmd = redis["command"]
        assert "$$REDIS_PASSWORD" in cmd, "REDIS_PASSWORD should be read inside the container"
        assert "${REDIS_PASSWORD" not in cmd, "command must not depend on root .env interpolation"

    def test_redis_conf_is_mounted(self):
        data = _load()
        mounts = [v.split(":")[0] for v in data["services"]["redis"]["volumes"]]
        assert "./infra/redis/redis.conf" in mounts, "redis.conf should be mounted"


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
                "REDIS_PASSWORD",
                "MINIO_ACCESS_KEY",
                "MINIO_SECRET_KEY",
            },
        )
        assert "MINIO_ROOT_PASSWORD" not in minio["environment"]


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
        assert "redis:6379" in content
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

        assert "COPY pyproject.toml uv.lock README.md ./" in content

    def test_has_non_root_user(self):
        content = DOCKERFILE_PATH.read_text()
        assert "USER appuser" in content, "Must run as non-root user (spec §17.5-4)"
        assert "useradd" in content or "adduser" in content, "Must create appuser"
        assert "ENV HOME=/home/appuser" in content
        assert "mkdir -p /app/var /home/appuser" in content

    def test_runtime_runs_alembic_before_gunicorn(self):
        content = DOCKERFILE_PATH.read_text()
        assert "alembic upgrade head && exec gunicorn" in content

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
