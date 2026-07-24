"""Drift guards for backend-port unification, Celery MySQL transport, and
compose network/exposure invariants.

Parse-based (yaml + text) — fast, deterministic, no Docker/network. These lock
invariants established by adversarial review so future edits can't silently
regress them.

(kkFileView-specific guards were removed together with the kkFileView service;
this file retains only the still-relevant platform invariants.)
"""

from __future__ import annotations

import sys

import yaml

from tests.support.paths import REPO_ROOT

COMPOSE = REPO_ROOT / "compose.yaml"
NGINX_DOCKER = REPO_ROOT / "infra/gateway/nginx/nginx.conf"
NGINX_LOCAL = REPO_ROOT / "infra/gateway/nginx/nginx.local.conf"

BACKEND_PORT = "8010"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ───────────────────────── network isolation / exposure ─────────────────────────
class TestNetworkIsolation:
    def test_internal_network_is_externally_isolated(self):
        assert _compose()["networks"]["internal"].get("internal") is True

    def test_only_nginx_publishes_ports(self):
        data = _compose()
        assert [n for n, s in data["services"].items() if s.get("ports")] == ["nginx"]

    def test_nginx_still_only_depends_on_backend(self):
        assert set(_compose()["services"]["nginx"].get("depends_on") or {}) == {"backend-api"}


# ───────────────────────── backend port unification ─────────────────────────
class TestBackendPortUnification:
    def test_no_stray_backend_8000_in_shipped_config(self):
        shipped_paths = [
            COMPOSE,
            NGINX_DOCKER,
            NGINX_LOCAL,
            REPO_ROOT / "backend/Dockerfile",
            REPO_ROOT / "Makefile",
            REPO_ROOT / "frontend/.env.example",
        ]
        local_production_env = REPO_ROOT / "frontend/.env.production"
        if local_production_env.exists():
            shipped_paths.append(local_production_env)
        for path in shipped_paths:
            text = path.read_text(encoding="utf-8")
            assert ":8000" not in text and "8000/tcp" not in text, f"stray backend 8000 in {path}"

    def test_backend_upstreams_agree_on_8010(self):
        assert f"backend-api:{BACKEND_PORT}" in NGINX_DOCKER.read_text(encoding="utf-8")
        assert f"127.0.0.1:{BACKEND_PORT}" in NGINX_LOCAL.read_text(encoding="utf-8")

    def test_celery_broker_and_result_derive_from_mysql(self):
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        from app.platform.config.settings import Settings

        s = Settings(_env_file=None, mysql_password="pw")
        assert s.celery_broker_url.startswith("sqla+mysql+pymysql://")
        assert s.celery_result_backend.startswith("db+mysql+pymysql://")
