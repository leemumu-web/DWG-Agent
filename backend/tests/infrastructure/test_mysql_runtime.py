from __future__ import annotations

import tomllib

import yaml

from tests.support.paths import REPO_ROOT


def test_legacy_redis_modules_and_infra_are_removed():
    for relative in (
        "backend/app/core/redis_client.py",
        "backend/app/services/redis_memory.py",
        "backend/app/services/cache_service.py",
        "infra/redis",
    ):
        assert not (REPO_ROOT / relative).exists(), relative


def test_python_runtime_has_no_redis_dependency():
    pyproject = tomllib.loads((REPO_ROOT / "backend/pyproject.toml").read_text())
    dependencies = "\n".join(pyproject["project"]["dependencies"]).lower()
    lock = (REPO_ROOT / "backend/uv.lock").read_text().lower()

    for package in ("redis", "fakeredis", "hiredis"):
        assert package not in dependencies
        assert f'name = "{package}"' not in lock
    assert "flower" not in dependencies
    assert 'name = "flower"' not in lock


def test_compose_and_scripts_do_not_require_redis():
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text())
    assert "redis" not in compose["services"]
    assert "redis_data" not in compose.get("volumes", {})

    script_text = "\n".join(
        path.read_text()
        for path in sorted((REPO_ROOT / "scripts").glob("*.sh"))
    ).lower()
    assert "redis" not in script_text
    assert "6379" not in script_text


def test_sql_broker_deployment_does_not_offer_unsupported_flower_or_inspect_healthchecks():
    compose_text = (REPO_ROOT / "compose.yaml").read_text().lower()
    compose = yaml.safe_load(compose_text)

    assert "flower" not in compose["services"]
    for service_name, service in compose["services"].items():
        if service_name.startswith("worker-"):
            healthcheck = str(service.get("healthcheck", {})).lower()
            assert "inspect ping" not in healthcheck


def test_active_python_sources_do_not_import_redis_client():
    source = "\n".join(
        path.read_text()
        for path in sorted((REPO_ROOT / "backend/app").rglob("*.py"))
    )
    assert "app.core.redis_client" not in source
    assert "from redis" not in source
    assert "import redis" not in source
