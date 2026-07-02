"""Verify compose.yaml is valid YAML and has expected defensive defaults.

These are lightweight static checks — no Docker daemon required.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).parent.parent.parent / "compose.yaml"


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


class TestMinioService:
    def test_minio_env_has_fallbacks(self):
        data = _load()
        env = data["services"]["minio"]["environment"]
        assert ":-" in env.get("MINIO_ROOT_USER", "")
        assert ":-" in env.get("MINIO_ROOT_PASSWORD", "")
