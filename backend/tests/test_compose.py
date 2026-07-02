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


def _load():
    with open(COMPOSE_PATH) as f:
        return yaml.safe_load(f)


class TestComposeYamlValid:
    def test_is_parseable_yaml(self):
        assert _load() is not None

    def test_has_expected_services(self):
        data = _load()
        services = data.get("services", {})
        for name in ("nginx", "backend-api", "mysql", "redis", "minio"):
            assert name in services, f"Missing service: {name}"


class TestMysqlService:
    def test_mysql_env_has_fallbacks(self):
        data = _load()
        mysql_env = data["services"]["mysql"]["environment"]
        assert "MYSQL_PASSWORD" in mysql_env
        assert "MYSQL_ROOT_PASSWORD" in mysql_env
        # Both env vars should include :- fallback syntax
        assert ":-" in mysql_env["MYSQL_PASSWORD"]
        assert ":-" in mysql_env["MYSQL_ROOT_PASSWORD"]

    def test_mysql_volumes_include_init_sql(self):
        data = _load()
        volumes = data["services"]["mysql"]["volumes"]
        init_mounts = [v for v in volumes if "init.sql" in str(v)]
        assert len(init_mounts) >= 1, "init.sql should be mounted"

    def test_mysql_has_healthcheck(self):
        data = _load()
        hc = data["services"]["mysql"]["healthcheck"]
        assert "mysqladmin" in " ".join(hc["test"])


class TestRedisService:
    def test_redis_command_has_fallback(self):
        data = _load()
        cmd = data["services"]["redis"]["command"]
        assert ":-" in cmd, "REDIS_PASSWORD should have fallback"

    def test_redis_conf_is_mounted(self):
        data = _load()
        mounts = [v.split(":")[0] for v in data["services"]["redis"]["volumes"]]
        assert "./infra/redis/redis.conf" in mounts, "redis.conf should be mounted"


class TestMinioService:
    def test_minio_env_has_fallbacks(self):
        data = _load()
        env = data["services"]["minio"]["environment"]
        assert ":-" in env.get("MINIO_ROOT_USER", "")
        assert ":-" in env.get("MINIO_ROOT_PASSWORD", "")


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
