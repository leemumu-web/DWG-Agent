from __future__ import annotations

from fastapi.testclient import TestClient

import app.core.redis_client as redis_module
from app.main import app


class TestRootHealth:
    def test_ok_when_all_components_up(self):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "ok"
        assert data["components"]["database"]["status"] == "ok"
        assert data["components"]["redis"]["status"] == "ok"

    def test_degraded_when_redis_down(self, monkeypatch):
        monkeypatch.setattr(redis_module, "_redis_client", None)
        monkeypatch.setattr(redis_module, "_redis_available", False)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "degraded"
        assert data["components"]["redis"]["status"] == "unavailable"
        assert data["components"]["database"]["status"] == "ok"

    def test_component_keys_always_present(self):
        client = TestClient(app)
        response = client.get("/health")
        components = response.json()["data"]["components"]
        assert "api" in components
        assert "database" in components
        assert "redis" in components

    def test_http_200_even_when_degraded(self, monkeypatch):
        monkeypatch.setattr(redis_module, "_redis_client", None)
        monkeypatch.setattr(redis_module, "_redis_available", False)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200


class TestV1Health:
    def test_ok_when_all_components_up(self):
        client = TestClient(app)
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "ok"
        assert "components" in data

    def test_degraded_when_redis_down(self, monkeypatch):
        monkeypatch.setattr(redis_module, "_redis_client", None)
        monkeypatch.setattr(redis_module, "_redis_available", False)
        client = TestClient(app)
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "degraded"

    def test_component_keys_present(self):
        client = TestClient(app)
        response = client.get("/api/v1/health")
        components = response.json()["data"]["components"]
        assert "api" in components
        assert "database" in components
        assert "redis" in components
