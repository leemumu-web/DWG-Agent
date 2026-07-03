from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


class TestRootHealth:
    def test_health_returns_ok(self):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "ok"
        # BUG-4: component details are no longer exposed on the health endpoint.
        assert "components" not in data

    def test_health_always_returns_200(self, monkeypatch):
        """Health is a lightweight liveness probe — always returns 200."""
        import app.core.redis_client as redis_module

        monkeypatch.setattr(redis_module, "_redis_client", None)
        monkeypatch.setattr(redis_module, "_redis_available", False)
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ok"
