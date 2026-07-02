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


class TestComposeYamlValid:
    def test_is_parseable_yaml(self):
        assert _load() is not None

    def test_has_expected_services(self):
        data = _load()
        services = data.get("services", {})
        for name in ("nginx", "backend-api", "mysql", "redis", "minio"):
            assert name in services, f"Missing service: {name}"


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

    def test_has_non_root_user(self):
        content = DOCKERFILE_PATH.read_text()
        assert "USER appuser" in content, "Must run as non-root user (spec §17.5-4)"
        assert "useradd" in content or "adduser" in content, "Must create appuser"

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
